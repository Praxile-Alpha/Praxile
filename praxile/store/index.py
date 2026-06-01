from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class IndexStoreMixin:
    def reindex(self) -> None:
        self.reindex_all()
    def reindex_all(self) -> None:
        self._init_db()
        trajectory_rows: list[tuple[dict[str, Any], Path]] = []
        for trajectory_path in self.paths.trajectories.glob("*.json"):
            data = read_json(trajectory_path, {})
            if data:
                trajectory_rows.append((data, trajectory_path))
        proposal_rows: list[tuple[dict[str, Any], Path]] = []
        for status, directory in [
            ("pending", self.paths.proposals_pending),
            ("accepted", self.paths.proposals_accepted),
            ("rejected", self.paths.proposals_rejected),
        ]:
            for proposal_path in directory.glob("*.json"):
                proposal = read_json(proposal_path, {})
                if proposal:
                    proposal.setdefault("status", status)
                    proposal_rows.append((proposal, proposal_path))
        asset_stats = [
            stat
            for stat in (self._asset_stat_metadata(path) for path in self._iter_asset_files())
            if stat
        ]
        expected = {stat["path"] for stat in asset_stats}

        with self._connection() as conn:
            existing = {
                row["path"]: row
                for row in conn.execute("SELECT path, content_hash, mtime_ns, size FROM assets").fetchall()
            }
            fts_paths = self._fts_paths(conn) if self._fts_available(conn) else set()
            vector_rows = {
                row["path"]: row
                for row in conn.execute("SELECT path, provider, model, dims FROM asset_vectors").fetchall()
            }
            fts_available = self._fts_available(conn)

        settings = vector_settings(self.config)
        needs_upsert: set[str] = set()
        asset_metadata: list[dict[str, Any]] = []
        vector_records: dict[str, dict[str, Any] | None] = {}
        for stat in asset_stats:
            asset_path = stat["path"]
            current = existing.get(asset_path)
            fts_missing = fts_available and asset_path not in fts_paths
            vector_row = vector_rows.get(asset_path)
            vector_stale = (
                not vector_row
                or vector_row["provider"] != settings["provider"]
                or vector_row["model"] != settings.get("model")
                or int(vector_row["dims"]) != int(settings.get("dims") or 256)
            )
            maybe_unchanged = (
                current
                and int(current["mtime_ns"] or -1) == int(stat["mtime_ns"])
                and int(current["size"] or -1) == int(stat["size"])
            )
            if maybe_unchanged and not fts_missing and not vector_stale:
                continue
            metadata = self._asset_metadata(stat["absolute_path"], stat=stat)
            if not metadata:
                continue
            if (
                current
                and current["content_hash"] == metadata["content_hash"]
                and not fts_missing
                and not vector_stale
            ):
                continue
            needs_upsert.add(asset_path)
            asset_metadata.append(metadata)
            vector_records[asset_path] = self._asset_vector_record(metadata)

        with self._connection() as conn:
            for trajectory, trajectory_path in trajectory_rows:
                self._index_trajectory_row(conn, trajectory, trajectory_path)
            for proposal, proposal_path in proposal_rows:
                self._index_proposal_row(conn, proposal, proposal_path)
            for metadata in asset_metadata:
                if metadata["path"] in needs_upsert:
                    self._upsert_asset_metadata_conn(conn, metadata, vector_records.get(metadata["path"]))
            stale = sorted(path for path in existing if path not in expected)
            for asset_path in stale:
                conn.execute("DELETE FROM assets WHERE path = ?", (asset_path,))
                conn.execute("DELETE FROM asset_vectors WHERE path = ?", (asset_path,))
                if self._fts_available(conn):
                    conn.execute("DELETE FROM assets_fts WHERE path = ?", (asset_path,))
    def _iter_asset_files(self) -> list[Path]:
        roots = [
            (self.paths.state / "memory", "**/*.md"),
            (self.paths.state / "skills", "*/SKILL.md"),
            (self.paths.state / "evals", "**/*.md"),
            (self.paths.state / "rules", "**/*.md"),
            (self.paths.state / "experience" / "failures", "*.md"),
            (self.paths.state / "experience" / "patterns", "*.md"),
            (self.paths.trajectories, "*.json"),
        ]
        files: list[Path] = []
        for root, pattern in roots:
            if root.exists():
                files.extend(path for path in root.glob(pattern) if path.is_file())
        return sorted(files)
    def index_asset(self, path: Path) -> None:
        self._init_db()
        metadata = self._asset_metadata(path)
        vector_record = self._asset_vector_record(metadata) if metadata else None
        with self._connection() as conn:
            if not metadata:
                self._remove_asset_conn(conn, path)
                self._record_index_event_conn(conn, path, "remove_asset", processed=True)
                return
            self._upsert_asset_metadata_conn(conn, metadata, vector_record)
            self._record_index_event_conn(conn, path, "index_asset", processed=True)
    def remove_asset(self, path: Path) -> None:
        self._init_db()
        asset_path = self._asset_relative_path(path)
        if not asset_path:
            return
        with self._connection() as conn:
            conn.execute("DELETE FROM assets WHERE path = ?", (asset_path,))
            conn.execute("DELETE FROM asset_vectors WHERE path = ?", (asset_path,))
            if self._fts_available(conn):
                conn.execute("DELETE FROM assets_fts WHERE path = ?", (asset_path,))
            self._record_index_event_conn(conn, path, "remove_asset", processed=True)
    def mark_asset_dirty(self, path: Path, *, event: str = "manual_dirty") -> None:
        self._init_db()
        with self._connection() as conn:
            self._record_index_event_conn(conn, path, event, processed=False)
    def index_changed(self, *, limit: int = 200) -> dict[str, Any]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, path, event
                FROM asset_index_events
                WHERE processed = 0
                ORDER BY id
                LIMIT ?
                """,
                (max(1, int(limit or 200)),),
            ).fetchall()
        if not rows:
            return {"processed": 0, "indexed": 0, "removed": 0, "events": []}
        indexed = 0
        removed = 0
        event_ids: list[int] = []
        events: list[dict[str, Any]] = []
        for row in rows:
            event_ids.append(int(row["id"]))
            path = self.paths.root / str(row["path"])
            metadata = self._asset_metadata(path)
            vector_record = self._asset_vector_record(metadata) if metadata else None
            with self._connection() as conn:
                if metadata:
                    self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                    indexed += 1
                else:
                    self._remove_asset_conn(conn, path)
                    removed += 1
                conn.execute(
                    "UPDATE asset_index_events SET processed = 1, processed_at = ? WHERE id = ?",
                    (utc_now(), int(row["id"])),
                )
            events.append({"id": int(row["id"]), "path": row["path"], "event": row["event"]})
        return {"processed": len(event_ids), "indexed": indexed, "removed": removed, "events": events}
    def queue_changed_assets_from_scan(self) -> dict[str, Any]:
        self._init_db()
        stats = [stat for stat in (self._asset_stat_metadata(path) for path in self._iter_asset_files()) if stat]
        expected = {stat["path"] for stat in stats}
        with self._connection() as conn:
            existing = {
                row["path"]: row
                for row in conn.execute("SELECT path, mtime_ns, size FROM assets").fetchall()
            }
            queued = 0
            for stat in stats:
                row = existing.get(stat["path"])
                if not row or int(row["mtime_ns"] or -1) != int(stat["mtime_ns"]) or int(row["size"] or -1) != int(stat["size"]):
                    self._record_index_event_conn(conn, stat["absolute_path"], "scan_changed", processed=False)
                    queued += 1
            stale = sorted(path for path in existing if path not in expected)
            for asset_path in stale:
                self._record_index_event_conn(conn, self.paths.root / asset_path, "scan_removed", processed=False)
                queued += 1
        return {"scanned": len(stats), "queued": queued, "stale": stale}
    def _index_asset_conn(self, conn: sqlite3.Connection, path: Path) -> None:
        metadata = self._asset_metadata(path)
        if not metadata:
            self._remove_asset_conn(conn, path)
            return
        # Keep this legacy connection-scoped helper transaction-short: vector
        # extraction is intentionally performed by callers before opening a
        # write transaction.
        self._upsert_asset_metadata_conn(conn, metadata)
    def _remove_asset_conn(self, conn: sqlite3.Connection, path: Path) -> None:
        asset_path = self._asset_relative_path(path)
        if not asset_path:
            return
        conn.execute("DELETE FROM assets WHERE path = ?", (asset_path,))
        conn.execute("DELETE FROM asset_vectors WHERE path = ?", (asset_path,))
        if self._fts_available(conn):
            conn.execute("DELETE FROM assets_fts WHERE path = ?", (asset_path,))
    def _upsert_asset_metadata_conn(
        self,
        conn: sqlite3.Connection,
        metadata: dict[str, Any],
        vector_record: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        existing = conn.execute(
            """
            SELECT created_at, usage_count, positive_outcome_count, negative_outcome_count, last_used_at
            FROM assets
            WHERE path = ?
            """,
            (metadata["path"],),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        usage_count = int(existing["usage_count"] or 0) if existing else 0
        positive_outcome_count = int(existing["positive_outcome_count"] or 0) if existing else 0
        negative_outcome_count = int(existing["negative_outcome_count"] or 0) if existing else 0
        last_used_at = existing["last_used_at"] if existing else None
        conn.execute(
            """
            INSERT OR REPLACE INTO assets
            (path, type, title, content_hash, summary, tags, source_task_id, confidence, mtime_ns, size, status,
             usage_count, positive_outcome_count, negative_outcome_count, last_used_at,
             created_at, updated_at, last_indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["path"],
                metadata["type"],
                metadata["title"],
                metadata["content_hash"],
                metadata["summary"],
                metadata["tags"],
                metadata["source_task_id"],
                metadata["confidence"],
                metadata.get("mtime_ns"),
                metadata.get("size"),
                metadata.get("status", "active"),
                usage_count,
                positive_outcome_count,
                negative_outcome_count,
                last_used_at,
                created_at,
                now,
                now,
            ),
        )
        if self._fts_available(conn):
            conn.execute("DELETE FROM assets_fts WHERE path = ?", (metadata["path"],))
            conn.execute(
                "INSERT INTO assets_fts(path, title, content, tags, type) VALUES (?, ?, ?, ?, ?)",
                (
                    metadata["path"],
                    metadata["title"],
                    metadata["content"],
                    metadata["tags"],
                    metadata["type"],
                ),
            )
        if vector_record:
            self._upsert_asset_vector_record_conn(conn, vector_record)
        else:
            conn.execute("DELETE FROM asset_vectors WHERE path = ?", (metadata["path"],))
    def _record_index_event_conn(self, conn: sqlite3.Connection, path: Path, event: str, *, processed: bool) -> None:
        asset_path = self._asset_relative_path(path) or path.as_posix()
        now = utc_now()
        conn.execute(
            """
            INSERT INTO asset_index_events(path, event, processed, created_at, processed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (asset_path, event, 1 if processed else 0, now, now if processed else None),
        )
    def _asset_vector_record(self, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
        if not metadata:
            return None
        settings = vector_settings(self.config)
        provider = settings["provider"]
        model = settings.get("model")
        dims = int(settings.get("dims") or 256)
        try:
            vector = embed_text(
                f"{metadata['title']}\n{metadata['tags']}\n{metadata['summary']}",
                provider=provider,
                model=model,
                dims=dims,
            )
        except Exception:
            return None
        return {
            "path": metadata["path"],
            "content_hash": metadata["content_hash"],
            "provider": provider,
            "model": model,
            "dims": len(vector),
            "vector_json": json.dumps(vector),
        }
    def _upsert_asset_vector_record_conn(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO asset_vectors
            (path, content_hash, provider, model, dims, vector_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["path"],
                record["content_hash"],
                record["provider"],
                record.get("model"),
                record["dims"],
                record["vector_json"],
                utc_now(),
            ),
        )
    def _fts_paths(self, conn: sqlite3.Connection) -> set[str]:
        if not self._fts_available(conn):
            return set()
        return {row["path"] for row in conn.execute("SELECT path FROM assets_fts").fetchall()}
    def _asset_stat_metadata(self, path: Path) -> dict[str, Any] | None:
        if not path.exists() or not path.is_file():
            return None
        asset_path = self._asset_relative_path(path)
        if not asset_path:
            return None
        asset_type = self._asset_type(asset_path)
        if not asset_type:
            return None
        stat = path.stat()
        return {
            "path": asset_path,
            "type": asset_type,
            "absolute_path": path,
            "mtime_ns": int(stat.st_mtime_ns),
            "size": int(stat.st_size),
        }
    def _asset_metadata(self, path: Path, *, stat: dict[str, Any] | None = None) -> dict[str, Any] | None:
        stat = stat or self._asset_stat_metadata(path)
        if not stat:
            return None
        asset_path = stat["path"]
        asset_type = stat["type"]
        full_path = self.paths.root / asset_path
        if asset_type == "trajectory_summary":
            payload = read_json(full_path, {})
            if not payload:
                return None
            status = "active"
            lifecycle_metadata = self._asset_lifecycle_metadata(full_path)
            status = str(lifecycle_metadata.get("status", status))
            title = payload.get("user_task", Path(asset_path).stem)
            report = payload.get("reward_report", {})
            result = payload.get("result", {})
            content = json.dumps(
                {
                    "task_id": payload.get("task_id"),
                    "user_task": payload.get("user_task"),
                    "result": result,
                    "reward_overall": report.get("overall"),
                    "proposal_types": [item.get("type") for item in payload.get("experience_candidates", [])],
                },
                ensure_ascii=False,
            )
            source_task_id = payload.get("task_id")
            confidence = report.get("overall")
        else:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            status = "active"
            lifecycle_metadata = self._asset_lifecycle_metadata(full_path)
            if asset_type == "skill":
                try:
                    skill_metadata = read_json(full_path.parent / "metadata.json", {}) or {}
                except Exception:
                    return None
                if not isinstance(skill_metadata, dict):
                    skill_metadata = {}
                if isinstance(skill_metadata, dict):
                    lifecycle_metadata = {**skill_metadata, **lifecycle_metadata}
                status = str(skill_metadata.get("status", "active"))
            status = str(lifecycle_metadata.get("status", status))
            title = _title_from_content(content, Path(asset_path).stem)
            source_task_id = _source_task_from_content(content)
            confidence = lifecycle_metadata.get("confidence", _confidence_from_content(content))
        return {
            "path": asset_path,
            "type": asset_type,
            "title": title,
            "content": content,
            "summary": shorten(content.strip(), 1200),
            "tags": _tags_for_asset(asset_path, asset_type),
            "source_task_id": source_task_id,
            "confidence": confidence,
            "status": status,
            "content_hash": stable_hash(content + json.dumps(lifecycle_metadata, sort_keys=True, ensure_ascii=False), length=32),
            "mtime_ns": stat.get("mtime_ns"),
            "size": stat.get("size"),
        }
    def _asset_lifecycle_metadata(self, asset_path: Path) -> dict[str, Any]:
        sidecar = self._asset_metadata_sidecar(asset_path)
        data = read_json(sidecar, {}) if sidecar.exists() else {}
        if not isinstance(data, dict):
            return {}
        return data
    def _asset_relative_path(self, path: Path) -> str | None:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.paths.state.resolve())
        except ValueError:
            return None
        return resolved.relative_to(self.paths.root.resolve()).as_posix()
    def _asset_type(self, root_relative_path: str) -> str | None:
        try:
            rel = Path(root_relative_path).relative_to(PRAXILE_DIR).as_posix()
        except ValueError:
            return None
        parts = rel.split("/")
        if not parts:
            return None
        if parts[0] == "memory" and rel.endswith(".md"):
            return "memory"
        if parts[0] == "skills" and rel.endswith("/SKILL.md"):
            return "skill"
        if parts[:2] == ["evals", "checklists"] and rel.endswith(".md"):
            return "eval_checklist"
        if parts[:2] == ["evals", "regression-cases"] and rel.endswith(".md"):
            return "eval_case"
        if parts[:2] == ["rules", "frozen-boundaries"] and rel.endswith(".md"):
            return "frozen_boundary"
        if parts[:2] == ["rules", "architecture-gates"] and rel.endswith(".md"):
            return "architecture_gate"
        if parts[:2] == ["rules", "harness-rules"] and rel.endswith(".md"):
            return "harness_rule"
        if parts[:2] == ["experience", "failures"] and rel.endswith(".md"):
            return "failure_pattern"
        if parts[:2] == ["experience", "patterns"] and rel.endswith(".md"):
            return "project_pattern"
        if parts[:2] == ["experience", "trajectories"] and rel.endswith(".json") and Path(rel).name != "external_compat.jsonl":
            return "trajectory_summary"
        return None
    def _fts_available(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute("SELECT name FROM sqlite_master WHERE name = 'assets_fts'").fetchone()
        return bool(row)
    def index_status(self, *, scan: bool = False) -> dict[str, Any]:
        self._init_db()
        with self._connection() as conn:
            indexed = {row["path"] for row in conn.execute("SELECT path FROM assets").fetchall()}
            vector_indexed = {row["path"] for row in conn.execute("SELECT path FROM asset_vectors").fetchall()}
            fts_available = self._fts_available(conn)
            pending_events = int(conn.execute("SELECT COUNT(*) AS count FROM asset_index_events WHERE processed = 0").fetchone()["count"])
        expected: set[str] = set(indexed)
        missing: list[str] = []
        stale: list[str] = []
        if scan:
            stats = [stat for stat in (self._asset_stat_metadata(path) for path in self._iter_asset_files()) if stat]
            expected = {stat["path"] for stat in stats}
            missing = sorted(path for path in expected if path not in indexed)
            stale = sorted(path for path in indexed if path not in expected)
        settings = vector_settings(self.config)
        if settings["enabled"]:
            vector_missing = sorted(path for path in expected if path not in vector_indexed)
            vector_stale = sorted(path for path in vector_indexed if path not in expected)
        else:
            vector_missing = []
            vector_stale = sorted(path for path in vector_indexed if path not in expected)
        return {
            "assets_expected": len(expected),
            "assets_indexed": len(indexed),
            "vectors_indexed": len(vector_indexed),
            "vectors_missing": vector_missing,
            "vectors_stale": vector_stale,
            "missing": missing,
            "stale": stale,
            "pending_events": pending_events,
            "deep_scan": scan,
            "needs_rebuild": bool(missing or stale or vector_missing or vector_stale or pending_events),
            "fts_available": fts_available,
        }



class IndexRepository(StoreRepository):
    def status(self, *, scan: bool = False) -> dict[str, Any]:
        return self.store.index_status(scan=scan)

    def index_asset(self, path: Any) -> None:
        self.store.index_asset(path)

    def reindex_all(self) -> None:
        self.store.reindex_all()
