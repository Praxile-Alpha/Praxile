from __future__ import annotations

from pathlib import Path

import pytest

from praxile.config import Config
from praxile.evolution import EvolutionEngine
from praxile.snapshot import SnapshotManager
from praxile.store import ExperienceStore


def test_snapshot_create_list_and_rollback(tmp_path: Path) -> None:
    state = tmp_path / ".praxile"
    memory = state / "memory" / "project.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("before\n", encoding="utf-8")

    manager = SnapshotManager(state)
    snapshot = manager.create_snapshot(reason="before mutation")

    memory.write_text("after\n", encoding="utf-8")
    (state / "cache").mkdir()
    (state / "cache" / "temp.txt").write_text("cache", encoding="utf-8")

    rows = manager.list_snapshots()
    assert rows[0]["snapshot_id"] == snapshot["snapshot_id"]

    result = manager.rollback(snapshot["snapshot_id"])

    assert result["snapshot_id"] == snapshot["snapshot_id"]
    assert memory.read_text(encoding="utf-8") == "before\n"
    assert not (state / "cache").exists()
    assert (state / "snapshots" / snapshot["snapshot_id"]).exists()


def test_snapshot_missing_is_clear(tmp_path: Path) -> None:
    manager = SnapshotManager(tmp_path / ".praxile")
    with pytest.raises(FileNotFoundError, match="Snapshot not found"):
        manager.rollback("snap_missing")


def test_apply_proposal_creates_pre_apply_snapshot(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    proposal = EvolutionEngine(config)._proposal(
        source_task_id="task_snapshot",
        proposal_type="memory_update",
        title="Snapshot memory",
        reason="Verify snapshot before applying proposal.",
        risk_level="low",
        evidence=["Snapshot test evidence."],
        confidence=0.8,
        changes=[{"path": "memory/project.md", "operation": "append", "content": "Snapshot applied memory."}],
    )
    store.write_proposal(proposal)

    accepted = store.apply_proposal(proposal)

    snapshot_id = accepted.get("pre_apply_snapshot_id")
    assert snapshot_id
    assert (config.paths.snapshots / snapshot_id / "snapshot.json").exists()
    assert "Snapshot applied memory." in (config.paths.state / "memory" / "project.md").read_text(encoding="utf-8")
