from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from praxile.bounded_evolution import BoundedHarnessEvolution, FailurePathologyMiner
from praxile.config import Config
from praxile.eval import EvalSuite
from praxile.store import ExperienceStore
from praxile.validation_lab import ProposalValidationLab
from praxile.harness_components import HarnessComponentRegistry


def _store(root: Path) -> tuple[Config, ExperienceStore]:
    config = Config.load(root)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    return config, store


def _write_episode(config: Config, episode_id: str, task_id: str) -> None:
    path = config.paths.state / "experience" / "episodes" / f"{episode_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "episode_id": episode_id,
                "task_id": task_id,
                "category": "shell_resource_issue",
                "failure_signature": "blocked_action",
                "applies_when": ["A safe verification command is blocked."],
                "does_not_apply_when": ["The command is destructive."],
            }
        ),
        encoding="utf-8",
    )


def _suite(root: Path, candidate_path: str) -> EvalSuite:
    validation_tests = root / "bounded_validation_tests"
    validation_tests.mkdir(exist_ok=True)
    (validation_tests / "test_candidate.py").write_text(
        "from pathlib import Path\n"
        "import unittest\n\n"
        "class CandidatePresenceTest(unittest.TestCase):\n"
        "    def test_candidate_is_present(self):\n"
        f"        self.assertTrue(Path('.praxile/{candidate_path}').exists())\n",
        encoding="utf-8",
    )
    suite = root / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "name": "bounded",
                "cases": [
                    {
                        "name": "candidate-only",
                        "set_type": "sealed",
                        "input": {
                            "command": ["python", "-m", "unittest", "discover", "-s", "bounded_validation_tests"]
                        },
                        "expected": {"returncode": 0},
                        "metrics": ["returncode_match"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return EvalSuite.load(suite)


def test_pathology_archive_and_component_scoped_alternative(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)

    rows = miner.mine()
    proposal = miner.propose(rows[0]["pathology_id"])
    store.write_proposal(proposal)

    assert rows[0]["episode_count"] == 2
    assert rows[0]["distinct_task_count"] == 2
    assert rows[0]["component_id"] == "tool_policy"
    assert proposal["component_change"]["component_id"] == "tool_policy"
    assert proposal["applicability_scope"]
    assert proposal["anti_scope"]
    archive = json.loads((config.paths.state / "experience" / "harness" / "quality-diversity-archive.json").read_text())
    assert archive["entries"][0]["alternatives"][0]["proposal_id"] == proposal["proposal_id"]


def test_validation_records_component_experiment_and_accept_promotes_manifest(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)
    proposal = miner.propose(miner.mine()[0]["pathology_id"])
    store.write_proposal(proposal)

    report = ProposalValidationLab(config, store).validate(proposal, _suite(tmp_path, proposal["changes"][0]["path"]))
    persisted = store.find_proposal(proposal["proposal_id"])
    registry = HarnessComponentRegistry(config)
    rules_before = registry.describe("rules")["version"]
    tool_before = registry.describe("tool_policy")["version"]
    accepted = store.apply_proposal(persisted)

    assert report["status"] == "validated"
    assert report["experiment"]["component_id"] == "tool_policy"
    assert accepted["promotion"]["approved_by"] == "human"
    assert accepted["promotion"]["validation"]["status"] == "validated"
    assert accepted["promotion"]["rollback_target"]["snapshot_id"]
    manifest = json.loads((config.paths.state / "experience" / "harness" / "active-manifest.json").read_text())
    assert manifest["components"]["tool_policy"]["proposal_id"] == proposal["proposal_id"]
    assert registry.describe("rules")["version"] == rules_before
    assert registry.describe("tool_policy")["version"] != tool_before


def test_operational_regression_triggers_configured_automatic_rollback(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)
    proposal = miner.propose(miner.mine()[0]["pathology_id"])
    store.write_proposal(proposal)
    ProposalValidationLab(config, store).validate(proposal, _suite(tmp_path, proposal["changes"][0]["path"]))
    accepted = store.apply_proposal(store.find_proposal(proposal["proposal_id"]))
    target = config.paths.state / accepted["changes"][0]["path"]
    assert target.exists()

    events = BoundedHarnessEvolution(config, store).monitor(
        {
            "task_id": "regression",
            "loaded_assets": [{"path": accepted["changes"][0]["path"]}],
            "reward_report": {"overall": 0.2, "process_safety": 1.0, "regression_passed": False},
        },
        apply_rollback=True,
    )

    assert events[0]["trigger"] == "operational_regression"
    assert events[0]["applied"] is True
    assert not target.exists()
    assert store.find_proposal(proposal["proposal_id"])["status"] == "rolled_back"
    manifest = json.loads((config.paths.state / "experience" / "harness" / "active-manifest.json").read_text())
    component = manifest["components"]["tool_policy"]
    assert component["monitoring"]["status"] == "rolled_back"
    assert component["active_version"] == HarnessComponentRegistry(config).describe("tool_policy")["version"]
    assert component["rolled_back_from_version"] != component["active_version"]


def test_unattributed_regression_does_not_rollback_component(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)
    proposal = miner.propose(miner.mine()[0]["pathology_id"])
    store.write_proposal(proposal)
    ProposalValidationLab(config, store).validate(proposal, _suite(tmp_path, proposal["changes"][0]["path"]))
    accepted = store.apply_proposal(store.find_proposal(proposal["proposal_id"]))

    events = BoundedHarnessEvolution(config, store).monitor(
        {"task_id": "unrelated", "loaded_assets": [], "reward_report": {"overall": 0.1, "process_safety": 0.1, "regression_passed": False}},
        apply_rollback=True,
    )

    assert events == []
    assert (config.paths.state / accepted["changes"][0]["path"]).exists()


def test_experiment_export_is_redacted_by_default(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)
    proposal = miner.propose(miner.mine()[0]["pathology_id"])
    proposal["changes"][0]["content"] += "\nprivate-repository-secret"
    from praxile.harness_components import HarnessComponentRegistry
    proposal["component_change"] = HarnessComponentRegistry(config).component_change_for(proposal["type"], proposal["changes"])
    store.write_proposal(proposal)
    output = tmp_path / "bundle.zip"

    BoundedHarnessEvolution(config, store).export_bundle(proposal["proposal_id"], output)

    with zipfile.ZipFile(output) as archive:
        exported = archive.read("proposal.json").decode()
        manifest = json.loads(archive.read("manifest.json"))
    assert "private-repository-secret" not in exported
    assert "content_hash" in exported
    assert manifest["private_repository_content_included"] is False


def test_outer_anchor_and_private_export_are_blocked(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    proposal = {
        "proposal_id": "outer_anchor",
        "type": "harness_rule",
        "status": "proposed",
        "risk_level": "high",
        "target_files": ["rules/safety-policy.json"],
        "changes": [{"path": "rules/safety-policy.json", "operation": "write", "content": "{}"}],
    }
    proposal["component_change"] = HarnessComponentRegistry(config).component_change_for("harness_rule", proposal["changes"])
    with pytest.raises(PermissionError, match="frozen outer anchor"):
        store.write_proposal(proposal)
    _write_episode(config, "ep1", "task1")
    _write_episode(config, "ep2", "task2")
    miner = FailurePathologyMiner(config)
    safe = miner.propose(miner.mine()[0]["pathology_id"])
    store.write_proposal(safe)
    with pytest.raises(PermissionError, match="disabled by project policy"):
        BoundedHarnessEvolution(config, store).export_bundle(safe["proposal_id"], tmp_path / "private.zip", include_private=True)


def test_routing_proposal_uses_task_privacy_risk_cost_and_judge_reliability(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    for index, status in enumerate(["unavailable", "success", "invalid_action"]):
        trajectory = {
            "task_id": f"route_{index}",
            "start_time": f"2026-08-0{index + 1}T00:00:00+00:00",
            "end_time": f"2026-08-0{index + 1}T00:01:00+00:00",
            "user_task": "routing task",
            "task_analysis": {"task_type": "architecture", "privacy_sensitive": index == 0, "high_risk": True},
            "result": {"status": "completed", "summary": "done"},
            "cost": {"estimated_usd": 0.02 + index * 0.01},
            "model_routing": {"performance": [{"status": status, "provider": "cloud", "model": f"model-{index}"}]},
            "reward_report": {"overall": 0.5 + index * 0.1, "escalation": {"reasons": ["judge_disagreement"] if index == 2 else []}},
        }
        store.record_trajectory(trajectory)

    proposal = BoundedHarnessEvolution(config, store).routing_proposal()

    assert proposal is not None
    assert proposal["component_change"]["component_id"] == "model_routing"
    content = proposal["changes"][0]["content"]
    assert "Privacy/high-risk" in content
    assert "cost=" in content
    assert "judge_reliable=False" in content
