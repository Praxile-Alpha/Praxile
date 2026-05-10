from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .constants import PRAXILE_DIR
from .utils import file_lock, new_id, read_json, run_process, shorten, unified_diff, utc_now, write_json


DEFAULT_WORKSPACE_EXCLUDES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
}

PRAXILE_WORKSPACE_EXCLUDES = {
    "backups",
    "cache",
    "checkpoints",
    "db",
    "logs",
    "workspaces",
}


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    mode: str
    root: Path
    source_root: Path
    metadata_path: Path
    created_at: str
    status: str = "created"
    task_id: str | None = None
    label: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "mode": self.mode,
            "root": str(self.root),
            "source_root": str(self.source_root),
            "metadata_path": str(self.metadata_path),
            "created_at": self.created_at,
            "status": self.status,
            "task_id": self.task_id,
            "label": self.label,
            "error": self.error,
        }


class WorkspaceManager:
    def __init__(self, config: Config):
        self.config = config
        self.source_root = config.paths.root.resolve()
        self.root = config.paths.state / "workspaces"

    def create(self, *, mode: str, label: str | None = None) -> WorkspaceRecord:
        self.root.mkdir(parents=True, exist_ok=True)
        with file_lock(self.root / "manager.lock", timeout_seconds=30):
            return self._create_locked(mode=mode, label=label)

    def _create_locked(self, *, mode: str, label: str | None = None) -> WorkspaceRecord:
        mode = mode or "copy"
        if mode not in {"copy", "git-worktree"}:
            raise ValueError(f"Unsupported workspace isolation mode: {mode}")
        workspace_id = new_id("ws")
        workspace_dir = self.root / workspace_id
        workspace_root = workspace_dir / "root"
        metadata_path = workspace_dir / "metadata.json"
        workspace_dir.mkdir(parents=True, exist_ok=False)
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            mode=mode,
            root=workspace_root,
            source_root=self.source_root,
            metadata_path=metadata_path,
            created_at=utc_now(),
            label=label,
        )
        try:
            if mode == "copy":
                self._copy_project(workspace_root)
            else:
                self._create_git_worktree(workspace_root)
                self._copy_praxile_state(workspace_root)
            self._write_record(record)
            return record
        except Exception as exc:
            failed = WorkspaceRecord(
                workspace_id=workspace_id,
                mode=mode,
                root=workspace_root,
                source_root=self.source_root,
                metadata_path=metadata_path,
                created_at=record.created_at,
                status="failed",
                label=label,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            self._write_record(failed)
            raise

    def update(self, workspace_id: str, **updates: Any) -> WorkspaceRecord:
        record = self.get(workspace_id)
        if record is None:
            raise FileNotFoundError(f"No workspace record found: {workspace_id}")
        data = record.to_dict()
        data.update({key: value for key, value in updates.items() if value is not None})
        data["updated_at"] = utc_now()
        write_json(record.metadata_path, data)
        return _record_from_dict(data)

    def get(self, workspace_id: str) -> WorkspaceRecord | None:
        metadata_path = self.root / workspace_id / "metadata.json"
        data = read_json(metadata_path, {})
        return _record_from_dict(data) if isinstance(data, dict) and data else None

    def list(self) -> list[WorkspaceRecord]:
        if not self.root.exists():
            return []
        records: list[WorkspaceRecord] = []
        for path in sorted(self.root.glob("*/metadata.json")):
            data = read_json(path, {})
            if isinstance(data, dict) and data:
                records.append(_record_from_dict(data))
        records.sort(key=lambda item: item.created_at, reverse=True)
        return records

    def cleanup(self, *, all_workspaces: bool = False, status: str | None = None) -> dict[str, Any]:
        with file_lock(self.root / "manager.lock", timeout_seconds=30):
            removed: list[str] = []
            skipped: list[str] = []
            for record in self.list():
                if not all_workspaces and status and record.status != status:
                    skipped.append(record.workspace_id)
                    continue
                if not all_workspaces and status is None and record.status not in {"completed", "failed"}:
                    skipped.append(record.workspace_id)
                    continue
                self._remove_record(record)
                removed.append(record.workspace_id)
            return {"removed": removed, "skipped": skipped}

    def remove(self, workspace_id: str) -> bool:
        with file_lock(self.root / "manager.lock", timeout_seconds=30):
            record = self.get(workspace_id)
            if record is None:
                return False
            self._remove_record(record)
            return True

    def write_diff_artifact(self, workspace_id: str, diff: str) -> Path | None:
        if not diff:
            return None
        path = self.config.paths.state / "experience" / "artifacts" / "workspaces" / f"{workspace_id}.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(diff, encoding="utf-8")
        return path

    def _copy_project(self, workspace_root: Path) -> None:
        excludes = set(DEFAULT_WORKSPACE_EXCLUDES)
        for item in self.config.get("workspace", "copy_excludes", default=[]):
            if isinstance(item, str) and item:
                excludes.add(item)
        shutil.copytree(self.source_root, workspace_root, ignore=self._ignore_factory(excludes), symlinks=False)

    def _ignore_factory(self, excludes: set[str]):
        source_root = self.source_root

        def ignore(directory: str, names: list[str]) -> set[str]:
            current = Path(directory).resolve()
            try:
                rel = current.relative_to(source_root)
            except ValueError:
                rel = Path(".")
            ignored: set[str] = set()
            for name in names:
                if name in excludes:
                    ignored.add(name)
                    continue
                parts = rel.parts
                if parts and parts[0] == PRAXILE_DIR and name in PRAXILE_WORKSPACE_EXCLUDES:
                    ignored.add(name)
                if parts[:2] == (PRAXILE_DIR, "workspaces"):
                    ignored.add(name)
            return ignored

        return ignore

    def _create_git_worktree(self, workspace_root: Path) -> None:
        workspace_root.parent.mkdir(parents=True, exist_ok=True)
        result = run_process(["git", "worktree", "add", "--detach", str(workspace_root), "HEAD"], cwd=self.source_root, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")

    def _copy_praxile_state(self, workspace_root: Path) -> None:
        source_state = self.config.paths.state
        target_state = workspace_root / PRAXILE_DIR
        target_state.mkdir(parents=True, exist_ok=True)
        for name in ["config.json", "constitution.md", "memory", "skills", "rules", "evals"]:
            source = source_state / name
            target = target_state / name
            if not source.exists():
                continue
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def _write_record(self, record: WorkspaceRecord) -> None:
        write_json(record.metadata_path, record.to_dict())

    def _remove_record(self, record: WorkspaceRecord) -> None:
        if record.mode == "git-worktree" and record.root.exists():
            try:
                run_process(["git", "worktree", "remove", "--force", str(record.root)], cwd=record.source_root, timeout=60)
            except Exception:
                pass
        workspace_dir = record.metadata_path.parent
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)


def workspace_diff_summary(source_root: Path, workspace_root: Path, *, max_chars: int = 30000) -> dict[str, Any]:
    source_root = source_root.resolve()
    workspace_root = workspace_root.resolve()
    source_files = _text_file_map(source_root)
    workspace_files = _text_file_map(workspace_root)
    changed: list[str] = []
    skipped_binary: list[str] = []
    diff_parts: list[str] = []
    insertions = 0
    deletions = 0
    for rel in sorted(set(source_files) | set(workspace_files)):
        left = source_files.get(rel)
        right = workspace_files.get(rel)
        if left == right:
            continue
        changed.append(rel)
        if left is None:
            before = ""
            after = right or ""
        elif right is None:
            before = left
            after = ""
        else:
            before = left
            after = right
        if "\x00" in before or "\x00" in after:
            skipped_binary.append(rel)
            continue
        item_diff = unified_diff(before, after, f"a/{rel}", f"b/{rel}")
        diff_parts.append(item_diff)
        for line in item_diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                insertions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1
    stat = f"{len(changed)} file(s) changed, {insertions} insertion(s), {deletions} deletion(s)"
    diff = "".join(diff_parts)
    return {
        "is_repo": False,
        "mode": "workspace_diff",
        "stat": stat,
        "diff": shorten(diff, max_chars),
        "files_changed": changed,
        "insertions": insertions,
        "deletions": deletions,
        "skipped_binary": skipped_binary,
        "source_root": str(source_root),
        "workspace_root": str(workspace_root),
    }


def _text_file_map(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _skip_diff_path(rel):
            continue
        try:
            result[rel] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return result


def _skip_diff_path(rel: str) -> bool:
    parts = Path(rel).parts
    if not parts:
        return True
    if parts[0] in {PRAXILE_DIR, ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
        return True
    return any(part in {"dist", "build", ".next"} for part in parts)


def _record_from_dict(data: dict[str, Any]) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=str(data["workspace_id"]),
        mode=str(data.get("mode") or "copy"),
        root=Path(str(data["root"])),
        source_root=Path(str(data["source_root"])),
        metadata_path=Path(str(data["metadata_path"])),
        created_at=str(data.get("created_at") or ""),
        status=str(data.get("status") or "created"),
        task_id=str(data["task_id"]) if data.get("task_id") else None,
        label=str(data["label"]) if data.get("label") else None,
        error=str(data["error"]) if data.get("error") else None,
    )
