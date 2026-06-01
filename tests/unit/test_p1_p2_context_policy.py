from __future__ import annotations

from pathlib import Path

import pytest

from praxile.config import Config
from praxile.gateway import GatewayApp
from praxile.security import SafetyPolicy
from praxile.services import ContextJuiceService, GovernanceLoopService, PolicyService, RepositoryMemoryTreeService, WorkflowService
from praxile.store import ExperienceStore

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


def _project(tmp_path: Path) -> tuple[Config, ExperienceStore]:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests" / "test_app.py").write_text("def test_add():\n    assert 1 + 2 == 3\n", encoding="utf-8")
    (tmp_path / "logs" / "ci.txt").write_text(
        "python -m pytest\nFAILED tests/test_app.py::test_add AssertionError\nsrc/app.py:2\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo\n\nContext project.\n", encoding="utf-8")
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    return config, store


def test_context_juice_compresses_files_with_evidence(tmp_path: Path) -> None:
    config, store = _project(tmp_path)
    payload = ContextJuiceService(config, store).compress_file("logs/ci.txt", role="reward_judge", write=True)

    assert payload["role"] == "reward_judge"
    assert payload["source_format"] == "test_log"
    assert payload["strategy"] == "test_log-preserve"
    assert payload["preserved_evidence"]["commands"]
    assert payload["preserved_evidence"]["file_paths"]
    assert payload["markdown_path"].endswith(".md")
    assert (config.paths.state / "context" / "summaries" / "latest.json").exists()


def test_repository_memory_tree_and_context_snapshot_are_written(tmp_path: Path) -> None:
    config, store = _project(tmp_path)

    tree = RepositoryMemoryTreeService(config, store).build(write=True)

    assert tree["module_count"] >= 1
    assert tree["markdown_path"] == ".praxile/context/tree/index.md"
    assert any(link["kind"] == "module" for link in tree["links"])
    assert any(link["href"].startswith("#/context/tree") for link in tree["links"])
    assert (config.paths.state / "context" / "tree" / "index.md").exists()
    assert (config.paths.state / "context" / "tree" / "specs" / "index.md").exists()
    assert (config.paths.state / "context" / "tree" / "failures" / "index.md").exists()
    assert (config.paths.state / "context" / "tree" / "decisions" / "architecture-boundaries.md").exists()
    assert (config.paths.state / "context" / "tree" / "timelines" / "unknown.md").exists()


def test_policy_layers_can_be_checked_seeded_and_explained(tmp_path: Path) -> None:
    config, _store = _project(tmp_path)
    service = PolicyService(config)

    seeded = service.write_defaults()
    check = service.check()
    explain = service.explain("proposal_gate")

    assert ".praxile/policies/default.json" in seeded
    assert check["ok"] is True
    assert explain["matched_layers"]
    assert explain["effective"]["human_approval_required"] is True


def test_policy_check_reports_invalid_json_without_crashing(tmp_path: Path) -> None:
    config, _store = _project(tmp_path)
    policy_dir = config.paths.state / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "project.json").write_text("{not-json", encoding="utf-8")

    check = PolicyService(config).check()

    assert check["ok"] is False
    assert any(error["layer"] == "project" and error["code"] == "invalid_json" for error in check["errors"])


def test_tool_policy_layer_blocks_runtime_tool_calls(tmp_path: Path) -> None:
    config, _store = _project(tmp_path)
    policy_dir = config.paths.state / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "tool_policy.json").write_text(
        """
        {
          "layer": "tool_policy",
          "tool_policy": {
            "rules": [
              {
                "id": "deny-policy-layer-command",
                "tool": "run_command",
                "message": "blocked by policy layer",
                "match": {"command_contains": ["--policy-layer-blocked"]}
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    decision = SafetyPolicy(config).check_tool_call("run_command", {"command": "python -m pytest --policy-layer-blocked"})

    assert decision.allowed is False
    assert decision.reason == "blocked by policy layer"


def test_workflow_templates_can_be_listed_shown_and_seeded(tmp_path: Path) -> None:
    config, _store = _project(tmp_path)
    service = WorkflowService(config)

    listed = service.list()
    shown = service.show("architecture-change")
    written = service.seed()

    assert any(item["name"] == "test-failure-repair" for item in listed["workflows"])
    assert shown["workflow"]["requires_spec"] == "required"
    assert ".praxile/workflows/architecture-change.json" in written


def test_workflow_templates_report_invalid_project_json(tmp_path: Path) -> None:
    config, _store = _project(tmp_path)
    workflow_dir = config.paths.state / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "broken.json").write_text("{not-json", encoding="utf-8")

    listed = WorkflowService(config).list()

    assert listed["workflows"]
    assert listed["errors"]
    assert listed["errors"][0]["path"] == ".praxile/workflows/broken.json"


def test_governance_loop_runs_safe_one_shot_pass(tmp_path: Path) -> None:
    config, store = _project(tmp_path)

    report = GovernanceLoopService(config, store).run_once(compress=True, run_audit=False, rebuild_graph=False)

    assert report["safety_contract"]["auto_accept_proposals"] is False
    assert report["context_snapshot"]["snapshot_id"]
    assert report["compression"]["compression_id"]
    assert (config.paths.state / "context" / "repo_snapshot.json").exists()
    assert (config.paths.state / "context" / "governance" / "latest.json").exists()


def test_gateway_exposes_context_policy_and_governance_routes(tmp_path: Path) -> None:
    _config, _store = _project(tmp_path)
    app = GatewayApp(tmp_path)

    assert app.dispatch("GET", "/api/context/juice/status")["profiles"]
    assert app.dispatch("POST", "/api/context/tree", payload={})["tree_id"]
    assert app.dispatch("GET", "/api/policies")["layers"]
    assert app.dispatch("POST", "/api/policies/check", payload={"write_defaults": True})["ok"] is True
    assert app.dispatch("GET", "/api/policies/proposal_gate")["matched_layers"]
    assert app.dispatch("GET", "/api/workflows")["workflows"]
    assert app.dispatch("GET", "/api/workflows/architecture-change")["workflow"]["requires_spec"] == "required"
    assert app.dispatch("POST", "/api/workflows/seed", payload={})["written"]
    report = app.dispatch("POST", "/api/governance/run-once", payload={"compress": True, "no_graph": True, "no_audit": True})
    assert report["safety_contract"]["edit_code"] is False
