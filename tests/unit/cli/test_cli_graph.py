from __future__ import annotations

from pathlib import Path

from praxile.cli import main


def test_graph_status_rebuild_cli(tmp_path: Path):
    assert main(["--project", str(tmp_path), "init", "--no-detect"]) == 0

    assert main(["--project", str(tmp_path), "graph", "status", "--rebuild"]) == 0
    assert main(["--project", str(tmp_path), "graph", "explain", ".praxile/memory/project.md"]) == 0

