from __future__ import annotations

import subprocess
from pathlib import Path

from praxile.adapters.mini_swe_preflight import run_mini_swe_preflight


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def test_preflight_accepts_exact_isolated_git_root(tmp_path: Path) -> None:
    _git_init(tmp_path)

    report = run_mini_swe_preflight(
        tmp_path,
        settings={"workspace_mode": "local", "require_git_workspace": True},
        config_specs=(),
    )

    assert report.passed is True
    assert report.git_root == str(tmp_path.resolve())


def test_preflight_rejects_parent_git_scope(tmp_path: Path) -> None:
    _git_init(tmp_path)
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)

    report = run_mini_swe_preflight(
        nested,
        settings={"workspace_mode": "local", "require_git_workspace": True},
        config_specs=(),
    )

    assert report.passed is False
    assert "patch scope" in report.blocking_reasons[0]


def test_preflight_allows_non_git_workspace_when_git_is_optional(tmp_path: Path) -> None:
    report = run_mini_swe_preflight(
        tmp_path,
        settings={"workspace_mode": "local"},
        config_specs=(),
    )

    assert report.passed is True
    git_check = next(item for item in report.checks if item["name"] == "git_workspace_root")
    assert git_check["status"] == "warning"
