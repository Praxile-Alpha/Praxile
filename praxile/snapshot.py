from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .utils import new_id, read_json, utc_now, write_json


SNAPSHOT_EXCLUDES = {"snapshots", "db", "cache", "__pycache__"}


class SnapshotManager:
    """Project-local .praxile snapshot manager.

    Snapshots are intended for governance rollback of durable experience state.
    They deliberately exclude generated indexes and caches; those can be rebuilt
    from restored Markdown/JSON assets.
    """

    def __init__(self, state_root: Path):
        self.state_root = state_root.resolve()
        self.snapshots_root = self.state_root / "snapshots"
        self.snapshots_root.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, *, reason: str = "", source: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.state_root.exists():
            raise FileNotFoundError(f"Praxile state root does not exist: {self.state_root}")
        snapshot_id = new_id("snap")
        target = self.snapshots_root / snapshot_id
        temp = self.snapshots_root / f".{snapshot_id}.tmp"
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(parents=True)
        for item in self.state_root.iterdir():
            if item.name in SNAPSHOT_EXCLUDES:
                continue
            dest = temp / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(item, dest)
        metadata = {
            "snapshot_id": snapshot_id,
            "created_at": utc_now(),
            "reason": reason,
            "source": source or {},
            "excluded": sorted(SNAPSHOT_EXCLUDES),
        }
        write_json(temp / "snapshot.json", metadata)
        temp.replace(target)
        return metadata | {"path": str(target)}

    def list_snapshots(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.snapshots_root.exists():
            return rows
        for path in sorted(self.snapshots_root.iterdir(), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            if not path.is_dir() or path.name.startswith("."):
                continue
            metadata = read_json(path / "snapshot.json", {})
            if not isinstance(metadata, dict):
                metadata = {}
            rows.append(
                {
                    "snapshot_id": metadata.get("snapshot_id") or path.name,
                    "created_at": metadata.get("created_at"),
                    "reason": metadata.get("reason", ""),
                    "source": metadata.get("source") or {},
                    "path": str(path),
                }
            )
        return rows

    def has_snapshot(self, snapshot_id: str) -> bool:
        return self._snapshot_path(snapshot_id).exists()

    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        source = self._snapshot_path(snapshot_id)
        if not source.exists() or not source.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        metadata = read_json(source / "snapshot.json", {})
        if not isinstance(metadata, dict):
            metadata = {}
        removed: list[str] = []
        restored: list[str] = []
        for item in list(self.state_root.iterdir()):
            if item.name == "snapshots":
                continue
            removed.append(item.name)
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in source.iterdir():
            if item.name == "snapshot.json":
                continue
            dest = self.state_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
            restored.append(item.name)
        return {
            "snapshot_id": metadata.get("snapshot_id") or snapshot_id,
            "created_at": metadata.get("created_at"),
            "reason": metadata.get("reason", ""),
            "removed": sorted(removed),
            "restored": sorted(restored),
        }

    def _snapshot_path(self, snapshot_id: str) -> Path:
        if "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in {"", ".", ".."}:
            raise ValueError(f"Invalid snapshot id: {snapshot_id}")
        return self.snapshots_root / snapshot_id
