from __future__ import annotations

import math
import shutil
import sqlite3
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .config import Config, ProjectPaths
from .constants import PRAXILE_DIR
from .feedback import feedback_reward
from .interop import PRAXILE_TRAJECTORY_SCHEMA, EXTERNAL_COMPAT_TRAJECTORY_FORMAT
from .utils import append_jsonl, file_lock, path_is_relative_to, read_json, shorten, stable_hash, utc_now, write_json
from .vector import cosine_similarity, embed_text, vector_settings


MEMORY_FILES = {
    "user": "User preferences and stable working habits.",
    "project": "Project facts, architecture notes, common commands, and stable context.",
    "decisions": "Accepted decisions with source task IDs and rationale.",
    "failures": "Failure patterns and repair notes that should not be repeated blindly.",
}

TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates" / "state"

PROPOSAL_ROOTS = {"memory", "skills", "evals", "rules", "experience"}
PROPOSAL_RULE_ROOTS = {"architecture-gates", "frozen-boundaries", "harness-rules"}
PROPOSAL_EVAL_ROOTS = {"checklists", "regression-cases"}
PROPOSAL_EXPERIENCE_ROOTS = {"failures", "patterns"}
ASSET_TYPE_PRIORITY = {
    "frozen_boundary": 0,
    "architecture_gate": 0,
    "harness_rule": 0,
    "rule": 0,
    "skill": 1,
    "eval_checklist": 2,
    "eval_case": 2,
    "project_pattern": 2,
    "failure_pattern": 3,
    "memory": 4,
    "trajectory_summary": 5,
}
ASSET_ACTIVE_STATUSES = {"active"}
ASSET_LIFECYCLE_STATUSES = {"active", "deprecated", "superseded", "archived"}


class ExperienceStore:
    def __init__(self, paths: ProjectPaths, config: Config | None = None):
        self.paths = paths
        self.config = config

    def initialize(self, config: Config, *, force: bool = False) -> None:
        self.config = config
        seeded_assets: list[Path] = []
        directories = [
            self.paths.state,
            self.paths.state / "memory",
            self.paths.state / "skills",
            self.paths.state / "experience" / "trajectories",
            self.paths.feedback,
            self.paths.state / "experience" / "failures",
            self.paths.state / "experience" / "patterns",
            self.paths.state / "experience" / "artifacts",
            self.paths.state / "experience" / "proposals" / "pending",
            self.paths.state / "experience" / "proposals" / "accepted",
            self.paths.state / "experience" / "proposals" / "rejected",
            self.paths.state / "evals" / "checklists",
            self.paths.state / "evals" / "regression-cases",
            self.paths.state / "rules" / "frozen-boundaries",
            self.paths.state / "rules" / "architecture-gates",
            self.paths.state / "rules" / "harness-rules",
            self.paths.state / "db",
            self.paths.state / "logs",
            self.paths.state / "backups",
            self.paths.state / "cache",
            self.paths.checkpoints,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        self._recover_interrupted_proposal_commits()

        if force or not self.paths.config.exists():
            config.write()

        for name, description in MEMORY_FILES.items():
            path = self.paths.state / "memory" / f"{name}.md"
            if force or not path.exists():
                path.write_text(
                    f"# {name.title()} Memory\n\n"
                    f"{description}\n\n"
                    "<!-- Accepted memory updates are appended below. -->\n",
                    encoding="utf-8",
                )
                seeded_assets.append(path)

        constitution_path = self.paths.state / "constitution.md"
        if force or not constitution_path.exists():
            self._write_template("constitution.md", constitution_path)
            seeded_assets.append(constitution_path)

        gate_path = self.paths.state / "rules" / "architecture-gates" / "default.md"
        if force or not gate_path.exists():
            self._write_template("rules/architecture-gates/default.md", gate_path)
            seeded_assets.append(gate_path)

        harness_rule_path = self.paths.state / "rules" / "harness-rules" / "default.md"
        if force or not harness_rule_path.exists():
            self._write_template("rules/harness-rules/default.md", harness_rule_path)
            seeded_assets.append(harness_rule_path)

        self._init_db()
        if force:
            self.reindex_all()
        else:
            for path in seeded_assets:
                self.index_asset(path)

    def _write_template(self, template_path: str, target: Path) -> None:
        source = TEMPLATE_ROOT / template_path
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _connect(self) -> sqlite3.Connection:
        self.paths.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.paths.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  user_task TEXT NOT NULL,
                  status TEXT NOT NULL,
                  reward_score REAL,
                  trajectory_path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                  proposal_id TEXT PRIMARY KEY,
                  source_task_id TEXT,
                  type TEXT NOT NULL,
                  title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  risk_level TEXT NOT NULL,
                  target_files TEXT NOT NULL,
                  path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_asset_schema(conn)
            self._ensure_graph_schema(conn)

    def _ensure_graph_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_nodes (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              ref_path TEXT,
              title TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_edges (
              edge_id TEXT PRIMARY KEY,
              source_node_id TEXT NOT NULL,
              target_node_id TEXT NOT NULL,
              relation_type TEXT NOT NULL,
              confidence REAL,
              evidence TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_source ON experience_edges(source_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_target ON experience_edges(target_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_relation ON experience_edges(relation_type)")

    def _ensure_asset_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'").fetchall()
        recreate = False
        if rows:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)").fetchall()}
            required = {
                "path",
                "type",
                "title",
                "content_hash",
                "summary",
                "tags",
                "source_task_id",
                "confidence",
                "mtime_ns",
                "size",
                "last_indexed_at",
                "usage_count",
                "positive_outcome_count",
                "negative_outcome_count",
                "last_used_at",
            }
            recreate = not required.issubset(columns)
        if recreate:
            conn.execute("DROP TABLE IF EXISTS assets")
            conn.execute("DROP TABLE IF EXISTS assets_fts")
            conn.execute("DROP TABLE IF EXISTS asset_vectors")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
              path TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              title TEXT,
              content_hash TEXT,
              summary TEXT,
              tags TEXT,
              source_task_id TEXT,
              confidence REAL,
              mtime_ns INTEGER,
              size INTEGER,
              status TEXT NOT NULL,
              usage_count INTEGER NOT NULL DEFAULT 0,
              positive_outcome_count INTEGER NOT NULL DEFAULT 0,
              negative_outcome_count INTEGER NOT NULL DEFAULT 0,
              last_used_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_indexed_at TEXT
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
                  path UNINDEXED,
                  title,
                  content,
                  tags,
                  type UNINDEXED
                )
                """
            )
        except sqlite3.OperationalError:
            conn.execute("DROP TABLE IF EXISTS assets_fts")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_vectors (
              path TEXT PRIMARY KEY,
              content_hash TEXT NOT NULL,
              provider TEXT NOT NULL,
              model TEXT,
              dims INTEGER NOT NULL,
              vector_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_index_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
              event TEXT NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              processed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
              task_id TEXT NOT NULL,
              matched_terms TEXT,
              matched_fields TEXT,
              why_loaded TEXT,
              score REAL,
              used_in_prompt INTEGER NOT NULL DEFAULT 1,
              referenced INTEGER NOT NULL DEFAULT 0,
              used_explicitly INTEGER NOT NULL DEFAULT 0,
              semantic_attribution TEXT,
              outcome TEXT NOT NULL DEFAULT 'unknown',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_columns(
            conn,
            "asset_usage",
            {
                "referenced": "INTEGER NOT NULL DEFAULT 0",
                "used_explicitly": "INTEGER NOT NULL DEFAULT 0",
                "semantic_attribution": "TEXT",
            },
        )

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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

    def record_asset_usage(
        self,
        task_id: str,
        assets: list[dict[str, Any]],
        *,
        used_in_prompt: bool = True,
        outcome: str = "unknown",
    ) -> None:
        if not assets:
            return
        self._init_db()
        now = utc_now()
        with self._connection() as conn:
            for item in assets:
                path = str(item.get("path") or "")
                if not path:
                    continue
                conn.execute(
                    """
                    INSERT INTO asset_usage
                    (path, task_id, matched_terms, matched_fields, why_loaded, score, used_in_prompt,
                     referenced, used_explicitly, outcome, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        path,
                        task_id,
                        json.dumps(item.get("matched_terms") or [], ensure_ascii=False),
                        json.dumps(item.get("matched_fields") or [], ensure_ascii=False),
                        item.get("why_loaded") or item.get("reason") or "",
                        item.get("final_score", item.get("score")),
                        1 if used_in_prompt else 0,
                        1 if item.get("referenced") else 0,
                        1 if item.get("used_explicitly") else 0,
                        outcome,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE assets
                    SET usage_count = usage_count + 1, last_used_at = ?, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, now, path),
                )

    def update_asset_usage_outcome(
        self,
        task_id: str,
        outcome: str,
        *,
        referenced_paths: list[str] | None = None,
        used_explicitly_paths: list[str] | None = None,
        attribution_results: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized = outcome if outcome in {"success", "failed", "needs_human", "unknown"} else "unknown"
        self._init_db()
        now = utc_now()
        referenced = set(referenced_paths or [])
        used_explicitly = set(used_explicitly_paths or [])
        attribution_by_path = {
            str(item.get("path") or ""): item
            for item in (attribution_results or [])
            if isinstance(item, dict) and str(item.get("path") or "")
        }
        with self._connection() as conn:
            for path, attribution in attribution_by_path.items():
                if attribution.get("referenced"):
                    referenced.add(path)
                if attribution.get("used_explicitly"):
                    used_explicitly.add(path)
                conn.execute(
                    """
                    UPDATE asset_usage
                    SET semantic_attribution = ?, updated_at = ?
                    WHERE task_id = ? AND path = ?
                    """,
                    (json.dumps(attribution, ensure_ascii=False), now, task_id, path),
                )
            for path in referenced | used_explicitly:
                conn.execute(
                    """
                    UPDATE asset_usage
                    SET referenced = CASE WHEN ? THEN 1 ELSE referenced END,
                        used_explicitly = CASE WHEN ? THEN 1 ELSE used_explicitly END,
                        updated_at = ?
                    WHERE task_id = ? AND path = ?
                    """,
                    (1 if path in referenced else 0, 1 if path in used_explicitly else 0, now, task_id, path),
                )
            rows = conn.execute(
                """
                SELECT path, MAX(referenced) AS referenced, MAX(used_explicitly) AS used_explicitly
                FROM asset_usage
                WHERE task_id = ? AND outcome = 'unknown'
                GROUP BY path
                """,
                (task_id,),
            ).fetchall()
            conn.execute(
                "UPDATE asset_usage SET outcome = ?, updated_at = ? WHERE task_id = ? AND outcome = 'unknown'",
                (normalized, now, task_id),
            )
            if normalized == "success":
                for row in rows:
                    attribution = attribution_by_path.get(str(row["path"]))
                    if attribution and not _attribution_allows_outcome_update(attribution, success=True):
                        continue
                    if not attribution and not (int(row["referenced"] or 0) or int(row["used_explicitly"] or 0)):
                        continue
                    conn.execute(
                        "UPDATE assets SET positive_outcome_count = positive_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )
            elif normalized == "failed":
                for row in rows:
                    attribution = attribution_by_path.get(str(row["path"]))
                    if attribution and not _attribution_allows_outcome_update(attribution, success=False):
                        continue
                    if not attribution and not (int(row["referenced"] or 0) or int(row["used_explicitly"] or 0)):
                        continue
                    conn.execute(
                        "UPDATE assets SET negative_outcome_count = negative_outcome_count + 1, updated_at = ? WHERE path = ?",
                        (now, row["path"]),
                    )

    def record_feedback(self, feedback: dict[str, Any]) -> Path:
        self._init_db()
        feedback_id = str(feedback["feedback_id"])
        path = self.paths.feedback / f"{feedback_id}.json"
        write_json(path, feedback)
        append_jsonl(self.paths.logs / "feedback.jsonl", {"event": "feedback_recorded", **feedback})
        target_type = str(feedback.get("target_type") or "")
        target_id = str(feedback.get("target_id") or "")
        sentiment = str(feedback.get("sentiment") or "neutral")
        if target_type == "asset":
            self._apply_asset_feedback(target_id, sentiment)
        elif target_type == "proposal":
            self._apply_proposal_feedback(target_id, feedback)
        elif target_type == "pattern":
            self._apply_pattern_feedback(target_id, feedback)
        return path

    def list_feedback(self, *, target_type: str | None = None, target_id: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.paths.feedback.exists():
            return rows
        for path in sorted(self.paths.feedback.glob("*.json")):
            item = read_json(path, {})
            if not isinstance(item, dict):
                continue
            if target_type and item.get("target_type") != target_type:
                continue
            if target_id and item.get("target_id") != target_id:
                continue
            rows.append(item)
        rows.sort(key=lambda item: item.get("created_at") or "")
        return rows

    def feedback_reward_for(self, target_type: str, target_id: str) -> dict[str, Any]:
        return feedback_reward(self.list_feedback(target_type=target_type, target_id=target_id))

    def _apply_asset_feedback(self, target_id: str, sentiment: str) -> None:
        normalized = target_id if target_id.startswith(f"{PRAXILE_DIR}/") else f"{PRAXILE_DIR}/{target_id}"
        now = utc_now()
        with self._connection() as conn:
            if sentiment == "positive":
                conn.execute(
                    """
                    UPDATE assets
                    SET positive_outcome_count = positive_outcome_count + 1, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, normalized),
                )
            elif sentiment == "negative":
                conn.execute(
                    """
                    UPDATE assets
                    SET negative_outcome_count = negative_outcome_count + 1, updated_at = ?
                    WHERE path = ?
                    """,
                    (now, normalized),
                )

    def _apply_proposal_feedback(self, target_id: str, feedback: dict[str, Any]) -> None:
        proposal = self.find_proposal(target_id)
        if not proposal:
            return
        counts = proposal.get("user_feedback") if isinstance(proposal.get("user_feedback"), dict) else {}
        sentiment = str(feedback.get("sentiment") or "neutral")
        if sentiment == "positive":
            counts["positive_count"] = int(counts.get("positive_count") or 0) + 1
            proposal["confidence"] = min(1.0, float(proposal.get("confidence") or 0.5) + 0.08)
        elif sentiment == "negative":
            counts["negative_count"] = int(counts.get("negative_count") or 0) + 1
            proposal["confidence"] = max(0.0, float(proposal.get("confidence") or 0.5) - 0.16)
            proposal.setdefault("feedback_influence", []).append(
                {
                    "type": "user_negative_feedback",
                    "feedback_id": feedback.get("feedback_id"),
                    "effect": "lowered_confidence_and_recommended_reject_or_edit",
                }
            )
            proposal["recommended_action_override"] = "reject_or_edit"
        else:
            counts["neutral_count"] = int(counts.get("neutral_count") or 0) + 1
        counts["last_feedback_id"] = feedback.get("feedback_id")
        counts["last_user_feedback_at"] = feedback.get("created_at") or utc_now()
        proposal["user_feedback"] = counts
        self.write_proposal(proposal)

    def _apply_pattern_feedback(self, target_id: str, feedback: dict[str, Any]) -> None:
        candidates = []
        raw = str(target_id or "").removeprefix(f"{PRAXILE_DIR}/")
        if raw:
            candidates.append(raw)
        if raw and not raw.endswith(".md"):
            candidates.append(f"experience/patterns/{raw}.md")
        sentiment = str(feedback.get("sentiment") or "neutral")
        for candidate in candidates:
            try:
                target = self._resolve_proposal_target(candidate)
            except PermissionError:
                continue
            if not target.exists():
                continue
            sidecar = self._asset_metadata_sidecar(target)
            current = read_json(sidecar, {}) if sidecar.exists() else {}
            if not isinstance(current, dict):
                current = {}
            positive = int(current.get("positive_feedback_count") or 0)
            negative = int(current.get("negative_feedback_count") or 0)
            if sentiment == "positive":
                positive += 1
            elif sentiment == "negative":
                negative += 1
            latest = current.get("latest_feedback") if isinstance(current.get("latest_feedback"), list) else []
            latest.append(
                {
                    "feedback_id": feedback.get("feedback_id"),
                    "sentiment": sentiment,
                    "raw_text": feedback.get("raw_text"),
                    "created_at": feedback.get("created_at") or utc_now(),
                }
            )
            current.update(
                {
                    "positive_feedback_count": positive,
                    "negative_feedback_count": negative,
                    "latest_feedback": latest[-10:],
                    "confidence_adjustment_from_feedback": round(min(0.18, positive * 0.04) - min(0.30, negative * 0.08), 4),
                    "updated_at": utc_now(),
                }
            )
            write_json(sidecar, current)
            self.index_asset(target)
            return

    def usage_for_task(self, task_id: str) -> list[dict[str, Any]]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM asset_usage
                WHERE task_id = ?
                ORDER BY id ASC
                """,
                (task_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["matched_terms"] = _decode_json_list(item.get("matched_terms"))
            item["matched_fields"] = _decode_json_list(item.get("matched_fields"))
            item["used_in_prompt"] = bool(item.get("used_in_prompt"))
            item["referenced"] = bool(item.get("referenced"))
            item["used_explicitly"] = bool(item.get("used_explicitly"))
            item["semantic_attribution"] = _decode_json_dict(item.get("semantic_attribution"))
            item["attribution_level"] = (
                _normalize_attribution_level(item["semantic_attribution"].get("attribution_level"))
                if isinstance(item.get("semantic_attribution"), dict)
                and item["semantic_attribution"].get("semantic_judge", {}).get("active")
                else _usage_attribution_level(item)
            )
            results.append(item)
        return results

    def attribution_history_for_asset(self, path: str, *, limit: int = 10) -> list[dict[str, Any]]:
        self._init_db()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT task_id, outcome, referenced, used_explicitly, semantic_attribution, created_at, updated_at
                FROM asset_usage
                WHERE path = ? AND semantic_attribution IS NOT NULL
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (path, max(1, int(limit))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["referenced"] = bool(item.get("referenced"))
            item["used_explicitly"] = bool(item.get("used_explicitly"))
            item["semantic_attribution"] = _decode_json_dict(item.get("semantic_attribution"))
            result.append(item)
        return result

    def rebuild_experience_graph(self) -> dict[str, Any]:
        self.reindex_all()
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[str, dict[str, Any]] = {}

        def add_node(node_id: str, node_type: str, *, ref_path: str | None = None, title: str | None = None, created_at: str | None = None) -> None:
            nodes.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "ref_path": ref_path,
                    "title": title or ref_path or node_id,
                    "created_at": created_at or utc_now(),
                },
            )

        def add_edge(
            source: str,
            target: str,
            relation_type: str,
            *,
            confidence: float = 1.0,
            evidence: dict[str, Any] | str | None = None,
        ) -> None:
            if source not in nodes or target not in nodes:
                return
            edge_id = "edge:" + stable_hash(f"{source}|{target}|{relation_type}|{json.dumps(evidence, sort_keys=True, ensure_ascii=False)}", length=24)
            edges.setdefault(
                edge_id,
                {
                    "edge_id": edge_id,
                    "source_node_id": source,
                    "target_node_id": target,
                    "relation_type": relation_type,
                    "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
                    "evidence": json.dumps(evidence or {}, ensure_ascii=False) if not isinstance(evidence, str) else evidence,
                    "created_at": utc_now(),
                },
            )

        with self._connection() as conn:
            asset_rows = [self._asset_row_with_lifecycle(row) for row in conn.execute("SELECT * FROM assets").fetchall()]
            task_rows = [dict(row) for row in conn.execute("SELECT * FROM tasks").fetchall()]
            usage_rows = [dict(row) for row in conn.execute("SELECT * FROM asset_usage").fetchall()]

        proposals = self.list_proposals(status=None, limit=10000)
        trajectories: dict[str, dict[str, Any]] = {}
        for row in task_rows:
            task_id = str(row.get("task_id") or "")
            if not task_id:
                continue
            trajectory_path = self.paths.root / str(row.get("trajectory_path") or "")
            trajectory = read_json(trajectory_path, {}) if trajectory_path.exists() else {}
            trajectories[task_id] = trajectory
            add_node(
                _graph_run_node_id(task_id),
                "run",
                ref_path=str(row.get("trajectory_path") or ""),
                title=str(row.get("user_task") or task_id),
                created_at=str(row.get("created_at") or utc_now()),
            )
            for spec_path in (trajectory.get("spec_context") or {}).get("spec_files") or []:
                spec_node = _graph_spec_node_id(str(spec_path))
                add_node(spec_node, "spec", ref_path=str(spec_path), title=Path(str(spec_path)).name)
                add_edge(spec_node, _graph_run_node_id(task_id), "derived_from_spec", evidence={"task_id": task_id})
            compliance = trajectory.get("spec_compliance") if isinstance(trajectory.get("spec_compliance"), dict) else {}
            for spec_path in compliance.get("spec_files") or []:
                spec_node = _graph_spec_node_id(str(spec_path))
                add_node(spec_node, "spec", ref_path=str(spec_path), title=Path(str(spec_path)).name)
                status = str(compliance.get("status") or "unknown")
                relation = "satisfies_spec" if status == "full" else "violates_spec" if status in {"partial", "failed"} else "derived_from_spec"
                confidence = _graph_confidence(compliance.get("score"), default=0.6)
                add_edge(
                    _graph_run_node_id(task_id),
                    spec_node,
                    relation,
                    confidence=confidence,
                    evidence={
                        "status": status,
                        "missing": len(compliance.get("missing") or []),
                        "violations": len(compliance.get("violations") or []),
                    },
                )
            action_counts_by_executor: dict[str, int] = {}
            for action in trajectory.get("actions") or []:
                executor = action.get("executor") if isinstance(action, dict) else {}
                if isinstance(executor, dict):
                    executor_id = str(executor.get("executor_id") or "")
                    if executor_id:
                        action_counts_by_executor[executor_id] = action_counts_by_executor.get(executor_id, 0) + 1
            for executor in trajectory.get("executors") or []:
                if not isinstance(executor, dict):
                    continue
                executor_id = str(executor.get("executor_id") or "")
                if not executor_id:
                    continue
                executor_node = _graph_executor_node_id(task_id, executor_id)
                add_node(
                    executor_node,
                    "executor",
                    ref_path=f"{task_id}:{executor_id}",
                    title=str(executor.get("role") or executor_id),
                    created_at=str(executor.get("registered_at") or row.get("created_at") or utc_now()),
                )
                add_edge(
                    executor_node,
                    _graph_run_node_id(task_id),
                    "participated_in_run",
                    confidence=1.0,
                    evidence={
                        "executor_id": executor_id,
                        "kind": executor.get("kind"),
                        "role": executor.get("role"),
                        "action_count": action_counts_by_executor.get(executor_id, 0),
                    },
                )

        for asset in asset_rows:
            path = str(asset.get("path") or "")
            if not path:
                continue
            node_id = _graph_asset_node_id(path)
            add_node(
                node_id,
                str(asset.get("type") or "asset"),
                ref_path=path,
                title=str(asset.get("title") or path),
                created_at=str(asset.get("created_at") or utc_now()),
            )
        for proposal in proposals:
            proposal_id = str(proposal.get("proposal_id") or "")
            if not proposal_id:
                continue
            proposal_node = _graph_proposal_node_id(proposal_id)
            add_node(
                proposal_node,
                "proposal",
                ref_path=str(proposal.get("path") or proposal_id),
                title=str(proposal.get("title") or proposal_id),
                created_at=str(proposal.get("created_at") or utc_now()),
            )
            source_task_id = str(proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id") or "")
            if source_task_id:
                add_node(_graph_run_node_id(source_task_id), "run", ref_path=None, title=source_task_id)
                add_edge(_graph_run_node_id(source_task_id), proposal_node, "generated_from_run", evidence={"proposal_type": proposal.get("type")})
            relation = "approved_by" if proposal.get("status") == "accepted" else "supports_proposal"
            for target in proposal.get("target_files") or []:
                asset_node = _graph_asset_node_id(_normalize_graph_asset_path(str(target)))
                if asset_node not in nodes:
                    add_node(asset_node, "asset", ref_path=_normalize_graph_asset_path(str(target)), title=str(target))
                add_edge(
                    proposal_node,
                    asset_node,
                    relation,
                    confidence=_graph_confidence(proposal.get("confidence"), default=0.7),
                    evidence={"proposal_id": proposal_id, "status": proposal.get("status"), "risk_level": proposal.get("risk_level")},
                )
            if proposal.get("type") == "asset_deprecate":
                for target in proposal.get("target_files") or []:
                    add_edge(proposal_node, _graph_asset_node_id(_normalize_graph_asset_path(str(target))), "deprecated_by", evidence={"proposal_id": proposal_id})
            for left, right, conflict_evidence in _graph_conflict_pairs(proposal):
                left_node = _graph_asset_node_id(left)
                right_node = _graph_asset_node_id(right)
                for asset_node, asset_path in [(left_node, left), (right_node, right)]:
                    if asset_node not in nodes:
                        add_node(asset_node, "asset", ref_path=_normalize_graph_asset_path(asset_path), title=asset_path)
                add_edge(
                    left_node,
                    right_node,
                    "contradicts_asset",
                    confidence=_graph_confidence(conflict_evidence.get("confidence"), default=0.65),
                    evidence={"proposal_id": proposal_id, **conflict_evidence},
                )

        for asset in asset_rows:
            path = str(asset.get("path") or "")
            replacement = str(asset.get("replaced_by") or "").strip()
            if path and replacement:
                add_node(_graph_asset_node_id(_normalize_graph_asset_path(replacement)), "asset", ref_path=_normalize_graph_asset_path(replacement), title=replacement)
                add_edge(
                    _graph_asset_node_id(_normalize_graph_asset_path(replacement)),
                    _graph_asset_node_id(path),
                    "supersedes",
                    confidence=0.9,
                    evidence={"reason": asset.get("superseded_reason") or asset.get("deprecated_reason") or asset.get("archived_reason")},
                )
        for row in usage_rows:
            path = str(row.get("path") or "")
            task_id = str(row.get("task_id") or "")
            if not path or not task_id:
                continue
            asset_node = _graph_asset_node_id(path)
            run_node = _graph_run_node_id(task_id)
            if asset_node not in nodes or run_node not in nodes:
                continue
            row_copy = dict(row)
            row_copy["used_in_prompt"] = bool(row_copy.get("used_in_prompt"))
            row_copy["referenced"] = bool(row_copy.get("referenced"))
            row_copy["used_explicitly"] = bool(row_copy.get("used_explicitly"))
            row_copy["semantic_attribution"] = _decode_json_dict(row_copy.get("semantic_attribution"))
            level = (
                _normalize_attribution_level(row_copy["semantic_attribution"].get("attribution_level"))
                if isinstance(row_copy.get("semantic_attribution"), dict)
                and row_copy["semantic_attribution"].get("semantic_judge", {}).get("active")
                else _usage_attribution_level(row_copy)
            )
            relation = "helped_run" if level in {"weak_positive", "strong_positive"} else "misled_run" if level in {"weak_negative", "harmful"} else "retrieved_in_run"
            add_edge(
                asset_node,
                run_node,
                relation,
                confidence=_graph_confidence(row_copy.get("score"), default=0.55),
                evidence={
                    "attribution_level": level,
                    "outcome": row_copy.get("outcome"),
                    "why_loaded": row_copy.get("why_loaded"),
                },
            )

        with self._connection() as conn:
            conn.execute("DELETE FROM experience_edges")
            conn.execute("DELETE FROM experience_nodes")
            for node in nodes.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experience_nodes(node_id, node_type, ref_path, title, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (node["node_id"], node["node_type"], node.get("ref_path"), node.get("title"), node["created_at"]),
                )
            for edge in edges.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experience_edges(edge_id, source_node_id, target_node_id, relation_type, confidence, evidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge["edge_id"],
                        edge["source_node_id"],
                        edge["target_node_id"],
                        edge["relation_type"],
                        edge["confidence"],
                        edge["evidence"],
                        edge["created_at"],
                    ),
                )
        return {"nodes": len(nodes), "edges": len(edges), "relation_counts": _count_graph_edges(edges.values())}

    def graph_status(self) -> dict[str, Any]:
        self._init_db()
        with self._connection() as conn:
            node_count = int(conn.execute("SELECT COUNT(*) AS count FROM experience_nodes").fetchone()["count"])
            edge_count = int(conn.execute("SELECT COUNT(*) AS count FROM experience_edges").fetchone()["count"])
            node_rows = conn.execute("SELECT node_type, COUNT(*) AS count FROM experience_nodes GROUP BY node_type ORDER BY node_type").fetchall()
            edge_rows = conn.execute("SELECT relation_type, COUNT(*) AS count FROM experience_edges GROUP BY relation_type ORDER BY relation_type").fetchall()
        return {
            "nodes": node_count,
            "edges": edge_count,
            "node_counts": {row["node_type"]: int(row["count"]) for row in node_rows},
            "relation_counts": {row["relation_type"]: int(row["count"]) for row in edge_rows},
        }

    def graph_explain(self, ref: str, *, depth: int = 1, limit: int = 100) -> dict[str, Any]:
        self._init_db()
        start = self._resolve_graph_node(ref)
        if not start:
            return {"ref": ref, "found": False, "nodes": [], "edges": []}
        max_depth = max(0, min(4, int(depth or 1)))
        max_edges = max(1, int(limit or 100))
        seen_nodes = {start["node_id"]}
        frontier = {start["node_id"]}
        collected_edges: dict[str, dict[str, Any]] = {}
        with self._connection() as conn:
            for _ in range(max_depth):
                if not frontier or len(collected_edges) >= max_edges:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM experience_edges
                    WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})
                    ORDER BY created_at DESC, relation_type
                    LIMIT ?
                    """,
                    [*frontier, *frontier, max_edges - len(collected_edges)],
                ).fetchall()
                next_frontier: set[str] = set()
                for row in rows:
                    edge = _graph_edge_row(row)
                    collected_edges[edge["edge_id"]] = edge
                    for node_id in [edge["source_node_id"], edge["target_node_id"]]:
                        if node_id not in seen_nodes:
                            seen_nodes.add(node_id)
                            next_frontier.add(node_id)
                frontier = next_frontier
            if seen_nodes:
                placeholders = ",".join("?" for _ in seen_nodes)
                node_rows = conn.execute(
                    f"SELECT * FROM experience_nodes WHERE node_id IN ({placeholders}) ORDER BY node_type, title",
                    list(seen_nodes),
                ).fetchall()
            else:
                node_rows = []
        nodes = [_graph_node_row(row) for row in node_rows]
        edges_list = list(collected_edges.values())
        return {
            "ref": ref,
            "found": True,
            "start_node": start,
            "nodes": nodes,
            "edges": edges_list,
            "relation_counts": _count_graph_edges(edges_list),
        }

    def _resolve_graph_node(self, ref: str) -> dict[str, Any] | None:
        candidates = _graph_node_candidates(ref)
        with self._connection() as conn:
            for node_id in candidates:
                row = conn.execute("SELECT * FROM experience_nodes WHERE node_id = ?", (node_id,)).fetchone()
                if row:
                    return _graph_node_row(row)
            row = conn.execute(
                """
                SELECT *
                FROM experience_nodes
                WHERE ref_path = ? OR title = ?
                ORDER BY CASE WHEN ref_path = ? THEN 0 ELSE 1 END, node_type
                LIMIT 1
                """,
                (ref, ref, ref),
            ).fetchone()
        return _graph_node_row(row) if row else None

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

    def record_trajectory(self, trajectory: dict[str, Any]) -> Path:
        task_id = trajectory["task_id"]
        date = trajectory["start_time"][:10]
        path = self.paths.trajectories / f"{date}-{task_id}.json"
        trajectory["schema"] = PRAXILE_TRAJECTORY_SCHEMA
        trajectory["external_compat"] = {
            "format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
            "path": str((self.paths.trajectories / "external_compat.jsonl").relative_to(self.paths.root)),
            "source_of_truth": str(path.relative_to(self.paths.root)),
            "note": "Praxile structured JSON is the audit source of truth; JSONL sidecar is for research/compression import.",
        }
        write_json(path, trajectory)
        append_jsonl(self.paths.trajectories / "external_compat.jsonl", self._to_external_compat_entry(trajectory, path))
        metadata = self._asset_metadata(path)
        vector_record = self._asset_vector_record(metadata) if metadata else None
        with self._connection() as conn:
            self._index_trajectory_row(conn, trajectory, path)
            if metadata:
                self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                self._record_index_event_conn(conn, path, "trajectory_recorded", processed=True)
        return path

    def _index_trajectory_row(self, conn: sqlite3.Connection, trajectory: dict[str, Any], path: Path) -> None:
        report = trajectory.get("reward_report") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks
            (task_id, user_task, status, reward_score, trajectory_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trajectory.get("task_id"),
                trajectory.get("user_task", ""),
                trajectory.get("result", {}).get("status", "unknown"),
                report.get("overall"),
                str(path.relative_to(self.paths.root)),
                trajectory.get("start_time", utc_now()),
                trajectory.get("end_time", trajectory.get("start_time", utc_now())),
            ),
        )

    def _to_external_compat_entry(self, trajectory: dict[str, Any], path: Path) -> dict[str, Any]:
        plan = "\n".join(f"- {item}" for item in trajectory.get("plan", []))
        actions = "\n".join(
            f"- #{item.get('step')} {item.get('action_type')} -> {item.get('status')}"
            for item in trajectory.get("actions", [])
        )
        report = trajectory.get("reward_report", {})
        route = trajectory.get("model_routing", {}).get("selected") or {}
        assistant_summary = (
            f"Plan:\n{plan or '(none)'}\n\n"
            f"Actions:\n{actions or '(none)'}\n\n"
            f"Result: {trajectory.get('result', {}).get('status')} — {trajectory.get('result', {}).get('summary')}\n"
            f"Reward overall: {report.get('overall')}"
        )
        return {
            "format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
            "source": "praxile",
            "source_task_id": trajectory.get("task_id"),
            "source_path": str(path.relative_to(self.paths.root)),
            "timestamp": trajectory.get("end_time") or trajectory.get("start_time"),
            "model": route.get("target"),
            "completed": trajectory.get("result", {}).get("status") == "completed",
            "reward": report.get("overall"),
            "conversations": [
                {"from": "human", "value": trajectory.get("user_task", "")},
                {"from": "gpt", "value": assistant_summary},
            ],
        }

    def latest_trajectory(self) -> dict[str, Any] | None:
        files = sorted(self.paths.trajectories.glob("*.json"))
        if not files:
            return None
        return read_json(files[-1], {})

    def get_trajectory(self, task_id: str) -> dict[str, Any] | None:
        path = self.find_trajectory_path(task_id)
        return read_json(path, {}) if path else None

    def find_trajectory_path(self, task_id: str) -> Path | None:
        for path in self.paths.trajectories.glob(f"*-{task_id}.json"):
            return path
        for path in self.paths.trajectories.glob("*.json"):
            data = read_json(path, {})
            if data.get("task_id", "").startswith(task_id):
                return path
        return None

    def update_trajectory(self, trajectory: dict[str, Any]) -> Path:
        task_id = str(trajectory.get("task_id") or "")
        path = self.find_trajectory_path(task_id)
        if path is None:
            date = str(trajectory.get("start_time") or utc_now())[:10]
            path = self.paths.trajectories / f"{date}-{task_id}.json"
        write_json(path, trajectory)
        metadata = self._asset_metadata(path)
        vector_record = self._asset_vector_record(metadata) if metadata else None
        with self._connection() as conn:
            self._index_trajectory_row(conn, trajectory, path)
            if metadata:
                self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                self._record_index_event_conn(conn, path, "trajectory_updated", processed=True)
        return path

    def write_proposal(self, proposal: dict[str, Any]) -> Path:
        proposal["updated_at"] = utc_now()
        directory = {
            "pending": self.paths.proposals_pending,
            "accepted": self.paths.proposals_accepted,
            "rejected": self.paths.proposals_rejected,
        }.get(proposal.get("status", "pending"), self.paths.proposals_pending)
        path = directory / f"{proposal['proposal_id']}.json"
        write_json(path, proposal)
        with self._connection() as conn:
            self._index_proposal_row(conn, proposal, path)
            self._record_index_event_conn(conn, path, "proposal_written", processed=True)
        return path

    def _index_proposal_row(self, conn: sqlite3.Connection, proposal: dict[str, Any], path: Path) -> None:
        target_files = ",".join(proposal.get("target_files") or [])
        conn.execute(
            """
            INSERT OR REPLACE INTO proposals
            (proposal_id, source_task_id, type, title, status, risk_level, target_files, path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal.get("proposal_id"),
                proposal.get("source_task_id"),
                proposal.get("type"),
                proposal.get("title", ""),
                proposal.get("status", "pending"),
                proposal.get("risk_level", "low"),
                target_files,
                str(path.relative_to(self.paths.root)),
                proposal.get("created_at", utc_now()),
                proposal.get("updated_at", proposal.get("created_at", utc_now())),
            ),
        )

    def _remove_proposal_row(self, proposal_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM proposals WHERE proposal_id = ?", (proposal_id,))

    def find_proposal(self, proposal_id: str | None = None, *, status: str | None = None) -> dict[str, Any] | None:
        dirs: list[Path]
        if status == "pending":
            dirs = [self.paths.proposals_pending]
        elif status == "accepted":
            dirs = [self.paths.proposals_accepted]
        elif status == "rejected":
            dirs = [self.paths.proposals_rejected]
        else:
            dirs = [self.paths.proposals_pending, self.paths.proposals_accepted, self.paths.proposals_rejected]

        candidates: list[Path] = []
        for directory in dirs:
            candidates.extend(sorted(directory.glob("*.json")))
        if proposal_id is None:
            if not candidates:
                return None
            return read_json(candidates[-1], {})

        for path in candidates:
            if path.stem == proposal_id or path.stem.startswith(proposal_id):
                return read_json(path, {})
            data = read_json(path, {})
            if data.get("proposal_id", "").startswith(proposal_id):
                return data
        return None

    def list_proposals(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status == "pending":
            dirs = [self.paths.proposals_pending]
        elif status == "accepted":
            dirs = [self.paths.proposals_accepted]
        elif status == "rejected":
            dirs = [self.paths.proposals_rejected]
        else:
            dirs = [self.paths.proposals_pending, self.paths.proposals_accepted, self.paths.proposals_rejected]
        proposals: list[dict[str, Any]] = []
        for directory in dirs:
            for path in sorted(directory.glob("*.json")):
                proposal = read_json(path, {})
                if proposal:
                    proposals.append(proposal)
        proposals.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        return proposals[:limit]

    def move_proposal(self, proposal: dict[str, Any], status: str) -> dict[str, Any]:
        old_status = proposal.get("status", "pending")
        proposal["status"] = status
        proposal["updated_at"] = utc_now()
        old_dir = {
            "pending": self.paths.proposals_pending,
            "accepted": self.paths.proposals_accepted,
            "rejected": self.paths.proposals_rejected,
        }.get(old_status, self.paths.proposals_pending)
        old_path = old_dir / f"{proposal['proposal_id']}.json"
        if old_path.exists():
            old_path.unlink()
        self._remove_proposal_row(proposal["proposal_id"])
        self.write_proposal(proposal)
        return proposal

    def apply_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        with file_lock(self.paths.state / "proposal-apply.lock"):
            return self._apply_proposal_locked(proposal)

    def _apply_proposal_locked(self, proposal: dict[str, Any]) -> dict[str, Any]:
        applied: list[dict[str, Any]] = []
        planned: list[dict[str, Any]] = []
        for change in proposal.get("changes", []):
            operation = change.get("operation", "write")
            if operation not in {"append", "write", "metadata_update"}:
                raise ValueError(f"Unsupported proposal operation: {operation}")
            asset_target = self._resolve_proposal_target(str(change["path"]))
            target = asset_target
            if operation == "metadata_update":
                target = self._asset_metadata_sidecar(asset_target)
            before_exists = target.exists()
            before = target.read_text(encoding="utf-8") if before_exists else ""
            if operation == "append":
                after = before.rstrip() + "\n\n" + change.get("content", "").rstrip() + "\n"
            elif operation == "write":
                after = change.get("content", "")
            else:
                metadata = self._normalized_lifecycle_metadata(change.get("metadata") or {})
                metadata.setdefault("source_proposal", proposal.get("proposal_id"))
                metadata.setdefault("updated_at", utc_now())
                current_metadata = read_json(target, {}) if target.exists() else {}
                if not isinstance(current_metadata, dict):
                    current_metadata = {}
                if metadata.get("status") == "active":
                    for key in [
                        "replaced_by",
                        "deprecated_reason",
                        "deprecated_at",
                        "superseded_reason",
                        "superseded_at",
                        "archived_reason",
                        "archived_at",
                    ]:
                        current_metadata.pop(key, None)
                event = _lifecycle_event_from_metadata(metadata, source=str(proposal.get("proposal_id") or "proposal"))
                if event:
                    current_events = current_metadata.get("lifecycle_events") if isinstance(current_metadata.get("lifecycle_events"), list) else []
                    current_metadata["lifecycle_events"] = [*current_events, event][-50:]
                current_metadata.update(metadata)
                after = json.dumps(current_metadata, indent=2, ensure_ascii=False) + "\n"
            planned.append(
                {
                    "change": change,
                    "target": target,
                    "index_target": asset_target,
                    "operation": operation,
                    "before_exists": before_exists,
                    "before": before,
                    "after": after,
                }
            )

        transaction_dir = self.paths.state / "cache" / "proposal-atomic" / proposal["proposal_id"]
        staging_dir = transaction_dir / "staged"
        backup_dir = transaction_dir / "before"
        journal_path = transaction_dir / "journal.json"
        if transaction_dir.exists():
            shutil.rmtree(transaction_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        staged: list[tuple[dict[str, Any], Path]] = []
        for index, item in enumerate(planned):
            staged_path = staging_dir / f"{index:04d}.tmp"
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(item["after"], encoding="utf-8")
            staged.append((item, staged_path))

        journal_changes: list[dict[str, Any]] = []
        for index, item in enumerate(planned):
            backup_name = f"{index:04d}.bak"
            backup_path = backup_dir / backup_name
            if item["before_exists"]:
                shutil.copy2(item["target"], backup_path)
            journal_changes.append(
                {
                    "target": item["target"].relative_to(self.paths.root).as_posix(),
                    "before_exists": item["before_exists"],
                    "backup": str(Path("before") / backup_name),
                }
            )
        write_json(
            journal_path,
            {
                "schema_version": 1,
                "proposal_id": proposal["proposal_id"],
                "phase": "prepared",
                "created_at": utc_now(),
                "changes": journal_changes,
            },
        )

        committed: list[dict[str, Any]] = []
        try:
            for item, staged_path in staged:
                target = item["target"]
                target.parent.mkdir(parents=True, exist_ok=True)
                staged_path.replace(target)
                committed.append(item)
            journal = read_json(journal_path, {})
            if isinstance(journal, dict):
                journal["phase"] = "files_committed"
                journal["updated_at"] = utc_now()
                write_json(journal_path, journal)
        except Exception:
            self._restore_proposal_transaction(transaction_dir)
            raise
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

        try:
            for item in planned:
                target = item["target"]
                target.parent.mkdir(parents=True, exist_ok=True)
                self.index_asset(item.get("index_target") or target)
                if target.name == "metadata.json" and (target.parent / "SKILL.md").exists():
                    self.index_asset(target.parent / "SKILL.md")
                applied.append(
                    {
                        "path": str(target.relative_to(self.paths.root)),
                        "before_exists": item["before_exists"],
                        "before": item["before"],
                        "after": item["after"],
                        "operation": item["operation"],
                        "index_path": str((item.get("index_target") or target).relative_to(self.paths.root)),
                        "applied_at": utc_now(),
                    }
                )

            proposal["applied_changes"] = applied
            proposal = self.move_proposal(proposal, "accepted")
            append_jsonl(
                self.paths.logs / "evolution.jsonl",
                {
                    "event": "proposal_accepted",
                    "proposal_id": proposal["proposal_id"],
                    "source_task_id": proposal.get("source_task_id"),
                    "applied_changes": [
                        {"path": item["path"], "before_exists": item["before_exists"]} for item in applied
                    ],
                    "created_at": utc_now(),
                },
            )
        except Exception:
            accepted_path = self.paths.proposals_accepted / f"{proposal['proposal_id']}.json"
            if not accepted_path.exists():
                self._restore_proposal_transaction(transaction_dir)
            raise
        else:
            if transaction_dir.exists():
                shutil.rmtree(transaction_dir, ignore_errors=True)
            return proposal

    def _recover_interrupted_proposal_commits(self) -> None:
        root = self.paths.state / "cache" / "proposal-atomic"
        if not root.exists():
            return
        for transaction_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            journal = read_json(transaction_dir / "journal.json", {})
            proposal_id = journal.get("proposal_id") if isinstance(journal, dict) else None
            accepted = bool(proposal_id and (self.paths.proposals_accepted / f"{proposal_id}.json").exists())
            if accepted:
                shutil.rmtree(transaction_dir, ignore_errors=True)
                continue
            self._restore_proposal_transaction(transaction_dir)
            append_jsonl(
                self.paths.logs / "evolution.jsonl",
                {
                    "event": "proposal_apply_recovered",
                    "proposal_id": proposal_id,
                    "created_at": utc_now(),
                },
            )

    def _restore_proposal_transaction(self, transaction_dir: Path) -> None:
        journal = read_json(transaction_dir / "journal.json", {})
        if not isinstance(journal, dict):
            shutil.rmtree(transaction_dir, ignore_errors=True)
            return
        for item in reversed(journal.get("changes", [])):
            try:
                target = (self.paths.root / item["target"]).resolve(strict=False)
                target.relative_to(self.paths.root.resolve())
            except (KeyError, ValueError):
                continue
            if item.get("before_exists"):
                backup = transaction_dir / str(item.get("backup", ""))
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)
            elif target.exists():
                if target.is_file() or target.is_symlink():
                    target.unlink()
        shutil.rmtree(transaction_dir, ignore_errors=True)

    def checkpoint_path(self, task_id: str) -> Path:
        return self.paths.checkpoints / f"{task_id}.json"

    def write_checkpoint(self, checkpoint: dict[str, Any]) -> Path:
        task_id = checkpoint["task_id"]
        path = self.checkpoint_path(task_id)
        checkpoint["updated_at"] = utc_now()
        write_json(path, checkpoint)
        return path

    def load_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        candidates = sorted(self.paths.checkpoints.glob(f"{task_id}*.json"))
        if not candidates:
            return None
        if len(candidates) == 1:
            return read_json(candidates[0], {})
        exact = self.checkpoint_path(task_id)
        if exact.exists():
            return read_json(exact, {})
        return read_json(candidates[-1], {})

    def delete_checkpoint(self, task_id: str) -> None:
        for path in self.paths.checkpoints.glob(f"{task_id}*.json"):
            try:
                path.unlink()
            except OSError:
                pass

    def _resolve_proposal_target(self, change_path: str) -> Path:
        raw_target = self.paths.state.joinpath(*self._proposal_path_parts(change_path))
        if raw_target.is_symlink():
            raise PermissionError(f"Proposal target must not be a symlink: {change_path}")
        target = raw_target.resolve(strict=False)
        state_root = self.paths.state.resolve()
        if not path_is_relative_to(target, state_root):
            raise PermissionError(f"Proposal target must stay inside {PRAXILE_DIR}/: {change_path}")
        parent = target.parent.resolve(strict=False)
        if not path_is_relative_to(parent, state_root):
            raise PermissionError(f"Proposal parent must stay inside {PRAXILE_DIR}/: {change_path}")
        if target.exists() and target.is_dir():
            raise PermissionError(f"Proposal target must be a file path, not a directory: {change_path}")
        return target

    def _proposal_path_parts(self, change_path: str) -> tuple[str, ...]:
        path_text = str(change_path)
        if not path_text or path_text != path_text.strip():
            raise PermissionError("Proposal path must be a non-empty trimmed relative path")
        if "\x00" in path_text or "\\" in path_text or ":" in path_text:
            raise PermissionError(f"Proposal path contains unsafe characters: {change_path}")
        if any(ord(char) < 32 for char in path_text):
            raise PermissionError(f"Proposal path contains control characters: {change_path}")
        windows_path = PureWindowsPath(path_text)
        if windows_path.drive or windows_path.root:
            raise PermissionError(f"Proposal path must not be absolute or drive-qualified: {change_path}")
        posix_path = PurePosixPath(path_text)
        if posix_path.is_absolute():
            raise PermissionError(f"Proposal path must be relative to {PRAXILE_DIR}/: {change_path}")
        parts = posix_path.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise PermissionError(f"Proposal path must not contain empty, dot, or parent segments: {change_path}")
        if any(part.startswith(PRAXILE_DIR) for part in parts):
            raise PermissionError(f"Proposal path must be relative inside {PRAXILE_DIR}/: {change_path}")
        root = parts[0]
        if root not in PROPOSAL_ROOTS:
            raise PermissionError(
                f"Proposal path must target memory, skills, evals, rules, experience/failures, or experience/patterns: {change_path}"
            )
        if root == "rules" and (len(parts) < 2 or parts[1] not in PROPOSAL_RULE_ROOTS):
            raise PermissionError(f"Proposal rule path targets an unsupported rules directory: {change_path}")
        if root == "evals" and (len(parts) < 2 or parts[1] not in PROPOSAL_EVAL_ROOTS):
            raise PermissionError(f"Proposal eval path targets an unsupported eval directory: {change_path}")
        if root == "experience" and (len(parts) < 2 or parts[1] not in PROPOSAL_EXPERIENCE_ROOTS):
            raise PermissionError(f"Proposal experience path may only target experience/failures or experience/patterns: {change_path}")
        if root == "skills":
            valid_skill_path = len(parts) == 3 and parts[2] in {"SKILL.md", "metadata.json"}
            valid_version_path = len(parts) == 4 and parts[2] == "versions" and parts[3].endswith(".md")
            if not (valid_skill_path or valid_version_path):
                raise PermissionError(
                    "Proposal skill path must be skills/<name>/SKILL.md, "
                    f"skills/<name>/metadata.json, or skills/<name>/versions/<version>.md: {change_path}"
                )
            if any(part.startswith(".") for part in parts[1:]):
                raise PermissionError(f"Proposal skill path must target visible skill files: {change_path}")
        if root == "memory" and (len(parts) < 2 or parts[-1].startswith(".")):
            raise PermissionError(f"Proposal memory path must target a visible memory file: {change_path}")
        return tuple(parts)

    def reject_proposal(self, proposal: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        if reason:
            proposal["rejection_reason"] = reason
        proposal["feedback"] = {
            "proposal_type": proposal.get("type"),
            "proposal_title": proposal.get("title"),
            "trigger_terms": _proposal_feedback_terms(proposal),
            "rejected_reason": reason,
            "source_task_type": proposal.get("source", {}).get("task_type") or proposal.get("task_type") or "unknown",
            "created_at": utc_now(),
        }
        proposal = self.move_proposal(proposal, "rejected")
        append_jsonl(
            self.paths.logs / "evolution.jsonl",
            {
                "event": "proposal_rejected",
                "proposal_id": proposal["proposal_id"],
                "reason": reason,
                "created_at": utc_now(),
            },
        )
        return proposal

    def rollback_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.find_proposal(proposal_id, status="accepted")
        if not proposal:
            raise FileNotFoundError(f"Accepted proposal not found: {proposal_id}")
        changes = list(proposal.get("applied_changes", []))
        for change in reversed(changes):
            target = self._resolve_recorded_state_path(str(change["path"]))
            if change.get("before_exists"):
                target.write_text(change.get("before", ""), encoding="utf-8")
            elif target.exists():
                target.unlink()
            index_path = change.get("index_path") or change.get("path")
            try:
                index_target = self._resolve_recorded_state_path(str(index_path))
            except PermissionError:
                index_target = target
            if index_target.exists():
                self.index_asset(index_target)
            else:
                self.remove_asset(index_target)
        proposal["status"] = "rolled_back"
        proposal["rolled_back_at"] = utc_now()
        write_json(self.paths.proposals_accepted / f"{proposal['proposal_id']}.json", proposal)
        with self._connection() as conn:
            self._index_proposal_row(conn, proposal, self.paths.proposals_accepted / f"{proposal['proposal_id']}.json")
        append_jsonl(
            self.paths.logs / "rollback.jsonl",
            {"event": "proposal_rollback", "proposal_id": proposal["proposal_id"], "created_at": utc_now()},
        )
        return proposal

    def _resolve_recorded_state_path(self, recorded_path: str) -> Path:
        raw = Path(recorded_path)
        if raw.is_absolute():
            raise PermissionError(f"Recorded state path must be relative: {recorded_path}")
        target = (self.paths.root / raw).resolve(strict=False)
        state_root = self.paths.state.resolve()
        if not path_is_relative_to(target, state_root):
            raise PermissionError(f"Recorded state path must stay inside {PRAXILE_DIR}/: {recorded_path}")
        return target

    def _asset_metadata_sidecar(self, asset_target: Path) -> Path:
        return asset_target.with_name(f"{asset_target.name}.meta.json")

    def _normalized_lifecycle_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("metadata_update requires a metadata object")
        result = dict(metadata)
        status = str(result.get("status", "active"))
        if status not in ASSET_LIFECYCLE_STATUSES:
            raise ValueError(f"Unsupported asset status: {status}")
        result["status"] = status
        return result

    def retrieve(self, query: str, *, kinds: list[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
        self._init_db()
        settings = vector_settings(self.config)
        try:
            with self._connection() as conn:
                if settings["enabled"] and settings["hybrid_enabled"]:
                    keyword = self._retrieve_fts(conn, query, kinds=kinds, limit=limit * 2)
                    vector = self._retrieve_vector(conn, query, kinds=kinds, limit=limit * 2)
                    return self._merge_retrieval_results(keyword, vector, limit=limit)
                if settings["enabled"]:
                    vector = self._retrieve_vector(conn, query, kinds=kinds, limit=limit)
                    if vector:
                        return vector
                return self._retrieve_fts(conn, query, kinds=kinds, limit=limit)
        except sqlite3.Error:
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)

    def _retrieve_vector(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        kinds: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        settings = vector_settings(self.config)
        try:
            query_vector = embed_text(
                query,
                provider=settings["provider"],
                model=settings.get("model"),
                dims=int(settings.get("dims") or 256),
            )
        except Exception:
            return []
        filters = _asset_type_filter(kinds)
        params: list[Any] = [settings["provider"], int(settings.get("dims") or 256)]
        where = "WHERE v.provider = ? AND v.dims = ? AND a.status = 'active'"
        model = settings.get("model")
        if model is None:
            where += " AND v.model IS NULL"
        else:
            where += " AND v.model = ?"
            params.append(model)
        if filters:
            where += " AND a.type IN (%s)" % ",".join("?" for _ in filters)
            params.extend(filters)
        rows = conn.execute(
            f"""
            SELECT
              a.path, a.type, a.title, a.summary, a.tags, a.source_task_id, a.confidence,
              a.usage_count, a.positive_outcome_count, a.negative_outcome_count, a.last_used_at,
              v.vector_json
            FROM asset_vectors v
            JOIN assets a ON a.path = v.path
            {where}
            """,
            params,
        ).fetchall()
        results: list[dict[str, Any]] = []
        min_score = float(self.config.get("retrieval", "vector_min_score", default=0.05) if self.config else 0.05)
        for row in rows:
            try:
                vector = json.loads(row["vector_json"])
            except json.JSONDecodeError:
                continue
            vector_score = cosine_similarity(query_vector, [float(value) for value in vector])
            if vector_score < min_score:
                continue
            asset_type = row["type"]
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            priority_boost = round((9 - priority) * self._retrieval_weight("vector_priority_weight", 0.03), 4)
            usage = self._usage_adjustment(row)
            base_score = vector_score + priority_boost
            score = round(max(0.0, base_score + usage["score_impact"]), 4)
            explanation = _retrieval_explanation(
                query,
                {
                    "path": row["path"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "content": row["summary"],
                },
                mode="vector",
                asset_type=asset_type,
            )
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "vector_score": round(vector_score, 4),
                    "fts_rank": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": score,
                    "retrieval_mode": "vector",
                    "matched_terms": explanation["matched_terms"],
                    "matched_fields": explanation["matched_fields"],
                    "why_loaded": explanation["why_loaded"],
                    "reason": f"Vector match in {asset_type}; provider={settings['provider']}; priority={priority}.",
                    "snippet": shorten(row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -item["score"], item["path"]))
        return results[:limit]

    def _merge_retrieval_results(
        self,
        keyword_results: list[dict[str, Any]],
        vector_results: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        rank_boost = self._retrieval_weight("hybrid_rank_boost", 1.0)
        for index, item in enumerate(keyword_results):
            copy = dict(item)
            copy["retrieval_mode"] = "hybrid_keyword"
            copy["hybrid_score"] = float(copy.get("score") or 0) + rank_boost / (index + 1)
            merged[copy["path"]] = copy
        for index, item in enumerate(vector_results):
            existing = merged.get(item["path"])
            boost = float(item.get("score") or 0) + rank_boost / (index + 1)
            if existing:
                existing["retrieval_mode"] = "hybrid"
                existing["vector_score"] = item.get("vector_score")
                existing["hybrid_score"] = float(existing.get("hybrid_score") or 0) + boost
                existing["final_score"] = round(float(existing.get("hybrid_score") or 0), 4)
                existing["reason"] = f"{existing.get('reason')} Vector corroborated the match."
                existing["why_loaded"] = f"{existing.get('why_loaded')} Vector similarity also matched."
            else:
                copy = dict(item)
                copy["retrieval_mode"] = "hybrid_vector"
                copy["hybrid_score"] = boost
                copy["final_score"] = round(float(boost), 4)
                merged[copy["path"]] = copy
        results = list(merged.values())
        results.sort(key=lambda item: (item["load_priority"], -float(item.get("hybrid_score") or 0), item["path"]))
        return results[:limit]

    def _retrieval_weight(self, key: str, default: float) -> float:
        try:
            return float(self.config.get("retrieval", key, default=default) if self.config else default)
        except (TypeError, ValueError):
            return default

    def _retrieve_fts(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        kinds: list[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._fts_available(conn):
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)
        fts_query = _fts_query(query)
        params: list[Any] = []
        where = ""
        if fts_query:
            where = "WHERE assets_fts MATCH ? AND a.status = 'active'"
            params.append(fts_query)
        else:
            where = "WHERE a.status = 'active'"
        filters = _asset_type_filter(kinds)
        if filters:
            where += (" AND " if where else "WHERE ") + "a.type IN (%s)" % ",".join("?" for _ in filters)
            params.extend(filters)
        sql = f"""
            SELECT
              a.path, a.type, a.title, a.summary, a.tags, a.source_task_id, a.confidence,
              a.usage_count, a.positive_outcome_count, a.negative_outcome_count, a.last_used_at,
              bm25(assets_fts) AS rank,
              snippet(assets_fts, 2, '', '', ' ... ', 12) AS snippet
            FROM assets_fts
            JOIN assets a ON a.path = assets_fts.path
            {where}
        """
        rows = conn.execute(sql, params).fetchall()
        if not rows and fts_query:
            return self._retrieve_fallback(query, kinds=kinds, limit=limit)
        results: list[dict[str, Any]] = []
        for row in rows:
            asset_type = row["type"]
            rank = float(row["rank"] or 0)
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            priority_boost = round((9 - priority) * self._retrieval_weight("keyword_priority_weight", 0.05), 4)
            usage = self._usage_adjustment(row)
            base_score = max(0.0, 1.0 / (1.0 + abs(rank))) + priority_boost
            score = round(max(0.0, base_score + usage["score_impact"]), 4)
            explanation = _retrieval_explanation(
                query,
                {
                    "path": row["path"],
                    "title": row["title"],
                    "tags": row["tags"],
                    "content": row["snippet"] or row["summary"],
                },
                mode="fts",
                asset_type=asset_type,
            )
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "fts_rank": round(rank, 6),
                    "vector_score": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": score,
                    "matched_terms": explanation["matched_terms"],
                    "matched_fields": explanation["matched_fields"],
                    "why_loaded": explanation["why_loaded"],
                    "reason": f"FTS match in {asset_type}; priority={priority}.",
                    "snippet": shorten(row["snippet"] or row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -item["score"], item["path"]))
        return results[:limit]

    def _retrieve_fallback(self, query: str, *, kinds: list[str] | None = None, limit: int = 6) -> list[dict[str, Any]]:
        filters = _asset_type_filter(kinds)
        with self._connection() as conn:
            if filters:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE status = 'active' AND type IN (%s)" % ",".join("?" for _ in filters),
                    filters,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM assets WHERE status = 'active'").fetchall()
        words = [word.lower() for word in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(word) > 1]
        results: list[dict[str, Any]] = []
        for row in rows:
            haystack = f"{row['path']} {row['title']} {row['summary']} {row['tags']}".lower()
            score = sum(haystack.count(word) for word in words) if words else 1
            if score <= 0:
                continue
            asset_type = row["type"]
            priority = ASSET_TYPE_PRIORITY.get(asset_type, 9)
            usage = self._usage_adjustment(row)
            priority_boost = round((9 - priority) * self._retrieval_weight("keyword_priority_weight", 0.05), 4)
            final_score = round(max(0.0, float(score) + priority_boost + usage["score_impact"]), 4)
            results.append(
                {
                    "kind": _kind_from_asset_type(asset_type),
                    "type": asset_type,
                    "path": row["path"],
                    "scope": "project",
                    "loaded_by": "praxile",
                    "load_priority": priority,
                    "score": score,
                    "fts_rank": None,
                    "vector_score": None,
                    "priority_boost": priority_boost,
                    "usage_count": usage["usage_count"],
                    "positive_outcome_count": usage["positive_outcome_count"],
                    "negative_outcome_count": usage["negative_outcome_count"],
                    "last_used_at": usage["last_used_at"],
                    "usage_score": usage["usage_score"],
                    "positive_boost": usage["positive_boost"],
                    "negative_penalty": usage["negative_penalty"],
                    "staleness_penalty": usage["staleness_penalty"],
                    "score_impact": usage["score_impact"],
                    "final_score": final_score,
                    "matched_terms": [word for word in words if word in haystack],
                    "matched_fields": ["metadata"],
                    "why_loaded": "Loaded by fallback metadata term matching.",
                    "reason": "Fallback metadata match.",
                    "snippet": shorten(row["summary"] or "", 1200),
                    "source_task_id": row["source_task_id"],
                    "confidence": row["confidence"],
                }
            )
        results.sort(key=lambda item: (item["load_priority"], -float(item.get("final_score") or item["score"]), item["path"]))
        return results[:limit]

    def _usage_adjustment(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        usage_count = _int_from_row(row, "usage_count")
        positive = _int_from_row(row, "positive_outcome_count")
        negative = _int_from_row(row, "negative_outcome_count")
        last_used_at = _value_from_row(row, "last_used_at")
        usage_score = round(math.log1p(max(0, usage_count)) * self._retrieval_weight("usage_log_weight", 0.02), 4)
        positive_boost = round(max(0, positive) * self._retrieval_weight("positive_outcome_weight", 0.10), 4)
        negative_penalty = round(max(0, negative) * self._retrieval_weight("negative_outcome_weight", 0.20), 4)
        staleness_penalty = 0.0
        if usage_count > 0 and last_used_at:
            parsed = _parse_iso_datetime(last_used_at)
            stale_days = int(self.config.get("retrieval", "stale_usage_days", default=90) if self.config else 90)
            if parsed and parsed <= datetime.now(timezone.utc) - timedelta(days=stale_days):
                staleness_penalty = round(self._retrieval_weight("stale_usage_penalty", 0.10), 4)
        score_impact = round(usage_score + positive_boost - negative_penalty - staleness_penalty, 4)
        return {
            "usage_count": usage_count,
            "positive_outcome_count": positive,
            "negative_outcome_count": negative,
            "last_used_at": last_used_at,
            "usage_score": usage_score,
            "positive_boost": positive_boost,
            "negative_penalty": negative_penalty,
            "staleness_penalty": staleness_penalty,
            "score_impact": score_impact,
        }

    def list_history(
        self,
        limit: int = 20,
        *,
        status: str | None = None,
        query: str | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("user_task LIKE ?")
            params.append(f"%{query}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([max(1, int(limit or 20)), max(0, int(offset or 0))])
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_assets(self, kind: str, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        asset_types = _asset_type_filter([kind]) or [kind]
        status_clause = "" if include_inactive else " AND status = 'active'"
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE type IN (%s)%s ORDER BY path ASC"
                % (",".join("?" for _ in asset_types), status_clause),
                asset_types,
            ).fetchall()
        return [self._asset_row_with_lifecycle(row) for row in rows]

    def get_asset(self, path: str) -> dict[str, Any] | None:
        self._init_db()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE path = ?", (path,)).fetchone()
        return self._asset_row_with_lifecycle(row) if row else None

    def _asset_row_with_lifecycle(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        sidecar = self._asset_metadata_sidecar(self.paths.root / str(item.get("path", "")))
        metadata = read_json(sidecar, {}) if sidecar.exists() else {}
        if isinstance(metadata, dict):
            item.update({key: value for key, value in metadata.items() if key not in {"path", "type", "title"}})
        return item

    def update_asset_status(
        self,
        path: str,
        *,
        status: str,
        replaced_by: str | None = None,
        reason: str | None = None,
        source: str = "manual_cli",
    ) -> dict[str, Any]:
        target = self._resolve_proposal_target(path.removeprefix(f"{PRAXILE_DIR}/"))
        metadata: dict[str, Any] = {
            "status": status,
            "updated_at": utc_now(),
            "source": source,
        }
        if status == "active":
            metadata["reactivated_at"] = utc_now()
        if replaced_by:
            metadata["replaced_by"] = replaced_by
        if reason:
            key = {
                "active": "reactivated_reason",
                "deprecated": "deprecated_reason",
                "superseded": "superseded_reason",
                "archived": "archived_reason",
            }.get(status, "reason")
            metadata[key] = reason
        metadata = self._normalized_lifecycle_metadata(metadata)
        sidecar = self._asset_metadata_sidecar(target)
        current = read_json(sidecar, {}) if sidecar.exists() else {}
        if not isinstance(current, dict):
            current = {}
        if status == "active":
            for key in [
                "replaced_by",
                "deprecated_reason",
                "deprecated_at",
                "superseded_reason",
                "superseded_at",
                "archived_reason",
                "archived_at",
            ]:
                current.pop(key, None)
        event = _lifecycle_event_from_metadata(metadata, source=source)
        if event:
            current_events = current.get("lifecycle_events") if isinstance(current.get("lifecycle_events"), list) else []
            current["lifecycle_events"] = [*current_events, event][-50:]
        current.update(metadata)
        write_json(sidecar, current)
        self.index_asset(target)
        return self.get_asset(str(target.relative_to(self.paths.root))) or {"path": str(target.relative_to(self.paths.root)), **current}

    def model_routing_stats(self, *, limit: int = 200) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.list_history(limit=limit):
            trajectory_path = self.paths.root / row.get("trajectory_path", "")
            trajectory = read_json(trajectory_path, {}) if trajectory_path.exists() else {}
            if not trajectory:
                continue
            task_type = trajectory.get("task_analysis", {}).get("task_type") or "unknown"
            selected = trajectory.get("model_routing", {}).get("selected") or {}
            target = selected.get("target") or "(none)"
            key = (task_type, target)
            group = groups.setdefault(
                key,
                {
                    "task_type": task_type,
                    "target": target,
                    "route_key": selected.get("route_key"),
                    "runs": 0,
                    "completed": 0,
                    "needs_human": 0,
                    "failed": 0,
                    "reward_total": 0.0,
                    "reward_count": 0,
                    "model_calls": 0,
                    "latency_ms_total": 0,
                    "performance_failures": 0,
                    "failure_patterns": {},
                },
            )
            group["runs"] += 1
            status = trajectory.get("result", {}).get("status", "unknown")
            if status in {"completed", "needs_human", "failed"}:
                group[status] += 1
            reward = trajectory.get("reward_report", {}).get("overall")
            if isinstance(reward, (int, float)):
                group["reward_total"] += float(reward)
                group["reward_count"] += 1
            calls = trajectory.get("model_routing", {}).get("calls") or []
            group["model_calls"] += len(calls)
            for call in calls:
                latency = call.get("latency_ms")
                if isinstance(latency, (int, float)):
                    group["latency_ms_total"] += int(latency)
            for event in trajectory.get("model_routing", {}).get("performance") or []:
                if event.get("status") in {"unavailable", "invalid_action", "error"}:
                    group["performance_failures"] += 1
                    pattern = event.get("failure_pattern") or event.get("status")
                    group["failure_patterns"][pattern] = group["failure_patterns"].get(pattern, 0) + 1

        stats: list[dict[str, Any]] = []
        for group in groups.values():
            runs = max(1, int(group["runs"]))
            reward_count = int(group["reward_count"])
            model_calls = int(group["model_calls"])
            stats.append(
                {
                    **group,
                    "completion_rate": round(group["completed"] / runs, 4),
                    "needs_human_rate": round(group["needs_human"] / runs, 4),
                    "failure_rate": round(group["failed"] / runs, 4),
                    "average_reward": round(group["reward_total"] / reward_count, 4) if reward_count else None,
                    "average_latency_ms": round(group["latency_ms_total"] / model_calls, 2) if model_calls else None,
                }
            )
        stats.sort(key=lambda item: (-item["runs"], item["task_type"], item["target"]))
        return stats

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

    def cleanup_empty_dirs(self, root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    def remove_all(self) -> None:
        if self.paths.state.exists():
            shutil.rmtree(self.paths.state)


def _title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def _source_task_from_content(content: str) -> str | None:
    match = re.search(r"source[_ -]task(?:_id)?[:` ]+`?([A-Za-z0-9_-]+)`?", content, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _confidence_from_content(content: str) -> float | None:
    match = re.search(r"confidence[:` ]+`?([0-9.]+)`?", content, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _tags_for_asset(path: str, asset_type: str) -> str:
    parts = Path(path).parts
    tags = [asset_type]
    tags.extend(part for part in parts if part not in {PRAXILE_DIR, "SKILL.md"} and "." not in part)
    return ",".join(dict.fromkeys(tags))


def _fts_query(query: str) -> str:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query) if token.strip()]
    if not tokens:
        return ""
    safe_tokens = []
    for token in tokens[:12]:
        if re.fullmatch(r"[A-Za-z0-9_]+", token):
            safe_tokens.append(f"{token}*")
        else:
            safe_tokens.append(f'"{token}"')
    return " OR ".join(safe_tokens)


def _query_terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1][:12]


def _retrieval_explanation(query: str, fields: dict[str, Any], *, mode: str, asset_type: str) -> dict[str, Any]:
    terms = _query_terms(query)
    matched_terms: list[str] = []
    matched_fields: list[str] = []
    for field_name, value in fields.items():
        lowered = str(value or "").lower()
        field_terms = [term for term in terms if term in lowered]
        if field_terms:
            normalized_field = "content" if field_name == "content" else field_name
            if normalized_field not in matched_fields:
                matched_fields.append(normalized_field)
            for term in field_terms:
                if term not in matched_terms:
                    matched_terms.append(term)
    if matched_terms and matched_fields:
        why = (
            f"matched task term(s) {', '.join(matched_terms[:5])} "
            f"in {', '.join(matched_fields)} for {asset_type} via {mode}"
        )
    else:
        why = f"loaded by {mode} score and {asset_type} priority"
    return {"matched_terms": matched_terms, "matched_fields": matched_fields, "why_loaded": why}


def _kind_from_asset_type(asset_type: str) -> str:
    if asset_type in {"frozen_boundary", "architecture_gate", "harness_rule", "rule"}:
        return "rule"
    if asset_type == "skill":
        return "skill"
    if asset_type in {"eval_checklist", "eval_case"}:
        return "eval"
    if asset_type == "memory":
        return "memory"
    if asset_type == "failure_pattern":
        return "failure"
    if asset_type == "project_pattern":
        return "pattern"
    return "trajectory"


def _asset_type_filter(kinds: list[str] | None) -> list[str]:
    if not kinds:
        return []
    expanded: list[str] = []
    mapping = {
        "rule": ["frozen_boundary", "architecture_gate", "harness_rule", "rule"],
        "skill": ["skill"],
        "eval": ["eval_checklist", "eval_case"],
        "memory": ["memory"],
        "failure": ["failure_pattern"],
        "pattern": ["project_pattern"],
        "experience": ["failure_pattern", "project_pattern"],
        "trajectory": ["trajectory_summary"],
    }
    for kind in kinds:
        expanded.extend(mapping.get(kind, [kind]))
    return list(dict.fromkeys(expanded))


def _decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _graph_asset_node_id(path: str) -> str:
    return f"asset:{_normalize_graph_asset_path(path)}"


def _graph_run_node_id(task_id: str) -> str:
    return f"run:{str(task_id or '').strip()}"


def _graph_proposal_node_id(proposal_id: str) -> str:
    return f"proposal:{str(proposal_id or '').strip()}"


def _graph_executor_node_id(task_id: str, executor_id: str) -> str:
    return f"executor:{str(task_id or '').strip()}:{str(executor_id or '').strip()}"


def _graph_spec_node_id(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return f"spec:{text}"


def _normalize_graph_asset_path(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    while text.startswith(f"{PRAXILE_DIR}/{PRAXILE_DIR}/"):
        text = text[len(PRAXILE_DIR) + 1 :]
    if not text.startswith(f"{PRAXILE_DIR}/"):
        text = f"{PRAXILE_DIR}/{text.lstrip('/')}"
    return text


def _graph_confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if math.isnan(parsed) or math.isinf(parsed):
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 4)


def _graph_node_candidates(ref: str) -> list[str]:
    text = str(ref or "").strip()
    if not text:
        return []
    candidates = []
    if text.startswith(("asset:", "run:", "proposal:", "spec:")):
        candidates.append(text)
        bare = text.split(":", 1)[1]
    else:
        bare = text
    candidates.extend(
        [
            _graph_asset_node_id(bare),
            _graph_proposal_node_id(bare),
            _graph_run_node_id(bare),
            _graph_spec_node_id(bare),
        ]
    )
    normalized = _normalize_graph_asset_path(bare)
    if normalized != bare:
        candidates.append(_graph_asset_node_id(normalized))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _graph_node_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    return {
        "node_id": item.get("node_id"),
        "node_type": item.get("node_type"),
        "ref_path": item.get("ref_path"),
        "title": item.get("title"),
        "created_at": item.get("created_at"),
    }


def _graph_edge_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    evidence = item.get("evidence")
    if isinstance(evidence, str) and evidence:
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            pass
    return {
        "edge_id": item.get("edge_id"),
        "source_node_id": item.get("source_node_id"),
        "target_node_id": item.get("target_node_id"),
        "relation_type": item.get("relation_type"),
        "confidence": item.get("confidence"),
        "evidence": evidence if evidence is not None else {},
        "created_at": item.get("created_at"),
    }


def _count_graph_edges(edges: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges or []:
        relation = edge.get("relation_type") if isinstance(edge, dict) else None
        if not relation:
            continue
        key = str(relation)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _graph_conflict_pairs(proposal: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    pairs: list[tuple[str, str, dict[str, Any]]] = []
    for field in ["conflicts", "contradictions", "conflicting_assets"]:
        raw_items = proposal.get(field)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            left = right = ""
            evidence: dict[str, Any] = {"field": field}
            if isinstance(item, dict):
                left = str(
                    item.get("left")
                    or item.get("source")
                    or item.get("source_path")
                    or item.get("asset")
                    or item.get("asset_path")
                    or ""
                )
                right = str(
                    item.get("right")
                    or item.get("target")
                    or item.get("target_path")
                    or item.get("other")
                    or item.get("other_path")
                    or ""
                )
                evidence.update({key: value for key, value in item.items() if key not in {"left", "right", "source", "target"}})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                left = str(item[0])
                right = str(item[1])
            if left and right and left != right:
                pairs.append((_normalize_graph_asset_path(left), _normalize_graph_asset_path(right), evidence))
    return pairs


def _attribution_allows_outcome_update(attribution: dict[str, Any], *, success: bool) -> bool:
    if attribution.get("should_update_asset_outcome") is False:
        return False
    level = _normalize_attribution_level(attribution.get("attribution_level"))
    if success:
        return level in {"weak_positive", "strong_positive", "mixed"}
    return level in {"weak_negative", "harmful", "mixed"}


def _usage_attribution_level(item: dict[str, Any]) -> str:
    outcome = str(item.get("outcome") or "unknown")
    if item.get("used_explicitly"):
        return "strong_positive" if outcome == "success" else "harmful" if outcome == "failed" else "referenced"
    if item.get("referenced"):
        return "weak_positive" if outcome == "success" else "weak_negative" if outcome == "failed" else "referenced"
    if item.get("used_in_prompt"):
        return "loaded_only" if outcome in {"success", "failed", "unknown", "needs_human"} else "loaded_only"
    return "none"


def _normalize_attribution_level(value: Any) -> str:
    text = str(value or "uncertain").strip().lower()
    aliases = {
        "medium_positive": "weak_positive",
        "medium_negative": "weak_negative",
        "strong_negative": "harmful",
        "explicit_unknown": "referenced",
        "referenced_unknown": "referenced",
    }
    return aliases.get(text, text)


def _proposal_feedback_terms(proposal: dict[str, Any]) -> list[str]:
    values: list[str] = [
        str(proposal.get("type") or ""),
        str(proposal.get("title") or ""),
        str(proposal.get("trigger_reason") or ""),
        str(proposal.get("future_applicability") or ""),
        str(proposal.get("applicability_scope") or ""),
    ]
    values.extend(str(value) for value in proposal.get("target_files") or [])
    values.extend(str(value) for value in proposal.get("affected_files") or [])
    values.extend(str(value) for value in proposal.get("evidence") or [])
    for change in proposal.get("changes") or []:
        if isinstance(change, dict):
            values.append(str(change.get("path") or ""))
            values.append(str(change.get("content") or ""))
    terms: list[str] = []
    for value in values:
        for token in re.findall(r"[A-Za-z0-9_\-/\.]+|[\u4e00-\u9fff]+", value.lower()):
            token = token.strip("`.,:;()[]{}")
            if len(token) > 2 and token not in {"memory", "skill", "proposal", "update", "experience"}:
                terms.append(token)
    return list(dict.fromkeys(terms))[:24]


def _lifecycle_event_from_metadata(metadata: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    status = metadata.get("status")
    if not status:
        return None
    reason = (
        metadata.get("reactivated_reason")
        or metadata.get("deprecated_reason")
        or metadata.get("superseded_reason")
        or metadata.get("archived_reason")
        or metadata.get("reason")
    )
    return {
        "status": status,
        "reason": reason,
        "replaced_by": metadata.get("replaced_by"),
        "source": source,
        "at": metadata.get("updated_at") or utc_now(),
    }


def _value_from_row(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (KeyError, IndexError):
        return default


def _int_from_row(row: sqlite3.Row | dict[str, Any], key: str) -> int:
    try:
        return int(_value_from_row(row, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
