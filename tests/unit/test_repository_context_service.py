from __future__ import annotations

from pathlib import Path

import pytest

from praxile.config import Config
from praxile.services import RepositoryContextService, format_context_status
from praxile.store import ExperienceStore

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


def test_repository_context_status_and_sync(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("from src.app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n\nProject context.\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    service = RepositoryContextService(config, store)

    status = service.status(refresh_map=True)

    assert status["repository"]["categories"]["source"]["count"] == 1
    assert status["repository"]["categories"]["tests"]["count"] == 1
    assert status["repository"]["categories"]["docs"]["count"] >= 1
    assert status["detected"]["test_commands"]
    assert status["freshness"]["level"] == "never_synced"
    assert status["health"]["signals"]["has_source"] is True
    assert status["health"]["signals"]["has_tests"] is True
    assert status["context_juice"]["strategy"] == "category-map-plus-sampled-paths"

    dry_run = service.sync(write=False)
    assert dry_run["path"] is None
    assert not (config.paths.state / "experience" / "context" / "latest.json").exists()

    snapshot = service.sync(write=True, since="7 days ago", include_ci=True, include_github=True)
    assert snapshot["path"].startswith(".praxile/experience/context/")
    assert (config.paths.state / "experience" / "context" / "latest.json").exists()
    assert (config.paths.state / "context" / "repo_snapshot.json").exists()
    assert (config.paths.state / "context" / "docs_index.json").exists()
    assert (config.paths.state / "context" / "specs_index.json").exists()
    assert (config.paths.state / "context" / "ci" / "index.json").exists()
    assert (config.paths.state / "context" / "github" / "context.json").exists()
    assert (config.paths.state / "context" / "commits" / "recent.json").exists()
    assert (config.paths.state / "context" / "commits" / "since.json").exists()
    assert (config.paths.state / "context" / "diffs" / "working_tree.json").exists()
    assert snapshot["status"]["sync_scope"]["since"] == "7 days ago"
    assert snapshot["working_tree_diff_path"] == ".praxile/context/diffs/working_tree.json"
    assert snapshot["status"]["github"]["network_fetch"] is False
    assert snapshot["status"]["github"]["pull_requests"] == []
    assert "Repository context:" in format_context_status(snapshot)
    synced = service.status()
    assert synced["freshness"]["last_sync_at"]
