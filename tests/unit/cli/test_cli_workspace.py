from __future__ import annotations

from pathlib import Path

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore


def test_run_workspace_copy_imports_trajectory_without_source_edits(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    assert main(["--project", str(tmp_path), "init", "--no-detect"]) == 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "run",
            "Inspect app in isolated workspace",
            "--workspace-mode",
            "copy",
            "--max-steps",
            "0",
            "--no-keep-workspace",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    trajectory = store.latest_trajectory()
    assert trajectory is not None
    isolation = trajectory["workspace_isolation"]
    assert isolation["mode"] == "copy"
    assert isolation["source_changes_applied"] is False
    assert not any((tmp_path / ".praxile" / "workspaces").glob("*/metadata.json"))

