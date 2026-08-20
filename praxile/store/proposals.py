from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403
from ..harness_components import HarnessComponentRegistry, is_harness_proposal


class ProposalsStoreMixin:
    def write_proposal(self, proposal: dict[str, Any]) -> Path:
        if is_harness_proposal(proposal):
            if self.config is None:
                raise ValueError("ExperienceStore must be initialized before writing a harness proposal")
            registry = HarnessComponentRegistry(self.config)
            if not isinstance(proposal.get("component_change"), dict):
                proposal["component_change"] = registry.component_change_for(
                    str(proposal.get("type") or ""),
                    proposal.get("changes") if isinstance(proposal.get("changes"), list) else [],
                )
            registry.validate_proposal(
                proposal,
                verify_versions=str(proposal.get("status") or "pending") not in {"accepted", "rolled_back"},
            )
            if proposal.get("status") == "pending":
                proposal["status"] = "proposed"
            proposal.setdefault("lifecycle_events", []).append(
                {
                    "status": proposal.get("status") or "proposed",
                    "created_at": utc_now(),
                    "reason": "harness proposal normalized by component registry",
                }
            )
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
                data = read_json(path, {})
                return data if _proposal_status_matches(data, status) else None
            data = read_json(path, {})
            if data.get("proposal_id", "").startswith(proposal_id) and _proposal_status_matches(data, status):
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
                if proposal and _proposal_status_matches(proposal, status):
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
        if is_harness_proposal(proposal) and bool(
            self.config.get("proposal_validation", "required_for_harness_components", default=True) if self.config else True
        ):
            component_change = HarnessComponentRegistry(self.config).validate_proposal(proposal)
            active_version = HarnessComponentRegistry(self.config).describe(str(component_change["component_id"]))["version"]
            if active_version != component_change.get("base_version"):
                raise PermissionError("Harness component changed after validation; rerun shadow validation against the active version")
            validation = proposal.get("validation") if isinstance(proposal.get("validation"), dict) else {}
            if proposal.get("status") != "validated" or validation.get("status") != "validated":
                raise PermissionError(
                    "Harness component proposals require a validated shadow report before human acceptance"
                )
            report_path = self.paths.root / str(validation.get("report_path") or "")
            report = read_json(report_path, {}) if report_path.exists() else {}
            if report.get("component_change") != component_change or report.get("status") != "validated":
                raise PermissionError("Validation report does not match the harness component candidate")
        with file_lock(self.paths.state / "proposal-apply.lock"):
            if not proposal.get("pre_apply_snapshot_id"):
                snapshot = SnapshotManager(self.paths.state).create_snapshot(
                    reason=f"before applying proposal {proposal.get('proposal_id')}",
                    source={
                        "type": "proposal",
                        "proposal_id": proposal.get("proposal_id"),
                        "source_task_id": proposal.get("source_task_id"),
                    },
                )
                proposal["pre_apply_snapshot_id"] = snapshot["snapshot_id"]
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
            proposal.setdefault("lifecycle_events", []).append(
                {"status": "accepted", "created_at": utc_now(), "reason": "human accepted validated proposal"}
            )
            proposal = self.move_proposal(proposal, "accepted")
            if is_harness_proposal(proposal):
                from ..bounded_evolution import BoundedHarnessEvolution
                proposal["promotion"] = BoundedHarnessEvolution(self.config, self).promotion_manifest(proposal)
                write_json(self.paths.proposals_accepted / f"{proposal['proposal_id']}.json", proposal)
                with self._connection() as conn:
                    self._index_proposal_row(conn, proposal, self.paths.proposals_accepted / f"{proposal['proposal_id']}.json")
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
        proposal.setdefault("lifecycle_events", []).append(
            {"status": "rolled_back", "created_at": proposal["rolled_back_at"], "reason": "proposal rollback completed"}
        )
        if is_harness_proposal(proposal):
            manifest_path = self.paths.state / "experience" / "harness" / "active-manifest.json"
            manifest = read_json(manifest_path, {})
            component_id = str((proposal.get("component_change") or {}).get("component_id") or "")
            active = (manifest.get("components") or {}).get(component_id)
            if isinstance(active, dict) and active.get("proposal_id") == proposal_id:
                rolled_back_from = active.get("active_version")
                active["rolled_back_from_version"] = rolled_back_from
                active["active_version"] = HarnessComponentRegistry(self.config).describe(component_id)["version"]
                active.setdefault("monitoring", {})["status"] = "rolled_back"
                active["monitoring"]["rolled_back_at"] = proposal["rolled_back_at"]
                manifest["updated_at"] = utc_now()
                write_json(manifest_path, manifest)
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



class ProposalsRepository(StoreRepository):
    def list(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_proposals(status=status, limit=limit)

    def find(self, proposal_id: str, *, status: str | None = None) -> dict[str, Any] | None:
        return self.store.find_proposal(proposal_id, status=status)

    def write(self, proposal: dict[str, Any]):
        return self.store.write_proposal(proposal)

    def apply(self, proposal: dict[str, Any]) -> dict[str, Any]:
        return self.store.apply_proposal(proposal)

    def reject(self, proposal: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
        return self.store.reject_proposal(proposal, reason=reason)


def _proposal_status_matches(proposal: dict[str, Any], requested: str | None) -> bool:
    if requested is None:
        return True
    actual = str(proposal.get("status") or "pending")
    if requested == "pending":
        return actual in {"pending", "proposed", "shadow_running", "validated", "inconclusive", "regressed"}
    return actual == requested
