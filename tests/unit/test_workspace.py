from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.workspace import WorkspaceManager, workspace_diff_summary


def test_workspace_copy_excludes_runtime_state_and_generates_diff(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    (tmp_path / ".praxile" / "db" / "junk.sqlite").write_text("db", encoding="utf-8")
    manager = WorkspaceManager(config)

    record = manager.create(mode="copy", label="test")
    try:
        assert (record.root / "app.py").exists()
        assert (record.root / ".praxile" / "config.json").exists()
        assert not (record.root / ".praxile" / "db").exists()
        assert not (record.root / ".praxile" / "workspaces").exists()

        (record.root / "app.py").write_text("value = 2\n", encoding="utf-8")
        diff = workspace_diff_summary(tmp_path, record.root)

        assert "app.py" in diff["files_changed"]
        assert "-value = 1" in diff["diff"]
        assert "+value = 2" in diff["diff"]
    finally:
        manager.remove(record.workspace_id)

