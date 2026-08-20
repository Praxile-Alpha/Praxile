from __future__ import annotations

import json
from pathlib import Path

import pytest

from praxile.config import Config
from praxile.eval import EvalSuite
from praxile.harness_components import HarnessComponentRegistry
from praxile.store import ExperienceStore
from praxile.validation_lab import ProposalValidationLab
from praxile.cli import main
from praxile.services import ProposalService


def _store(tmp_path: Path) -> tuple[Config, ExperienceStore]:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    validation_tests = tmp_path / "validation_tests"
    validation_tests.mkdir(exist_ok=True)
    (validation_tests / "test_skill.py").write_text(
        "from pathlib import Path\n"
        "import unittest\n\n"
        "class SkillPresenceTest(unittest.TestCase):\n"
        "    def test_skill_is_present(self):\n"
        "        self.assertTrue(Path('.praxile/skills/parser-repair/SKILL.md').exists())\n",
        encoding="utf-8",
    )
    return config, store


def _skill_proposal(config: Config) -> dict:
    changes = [
        {
            "path": "skills/parser-repair/SKILL.md",
            "operation": "write",
            "content": "# Parser Repair\n\nUse the verified parser repair sequence.\n",
        }
    ]
    component = HarnessComponentRegistry(config).component_change_for("skill_create", changes)
    return {
        "proposal_id": "prop_validation_skill",
        "source_task_id": "source_task",
        "type": "skill_create",
        "title": "Add parser repair skill",
        "status": "proposed",
        "risk_level": "low",
        "target_files": [changes[0]["path"]],
        "changes": changes,
        "component_change": component,
    }


def _suite(tmp_path: Path, *, expected_returncode: int = 0, set_type: str = "sealed") -> EvalSuite:
    suite_path = tmp_path / "validation-suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "skill activation shadow suite",
                "owner": "project_maintainer",
                "expected_owner": "independent_eval_owner",
                "cases": [
                    {
                        "name": "skill is present",
                        "set_type": set_type,
                        "owner": "project_maintainer",
                        "expected_owner": "independent_eval_owner",
                        "input": {
                            "command": ["python", "-m", "unittest", "discover", "-s", "validation_tests"]
                        },
                        "expected": {"returncode": expected_returncode},
                        "metrics": ["returncode_match"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return EvalSuite.load(suite_path)


def test_registry_versions_every_harness_component(tmp_path: Path) -> None:
    config, _store_instance = _store(tmp_path)

    manifest = HarnessComponentRegistry(config).manifest()

    assert {item["component_id"] for item in manifest["components"]} == {
        "prompts",
        "retrieval_policy",
        "skills",
        "rules",
        "model_routing",
        "tool_policy",
        "compression_profile",
        "stopping_policy",
        "eval_policy",
    }
    assert all(len(item["version"]) == 16 for item in manifest["components"])


def test_shadow_validation_improves_without_touching_active_harness(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    proposal = _skill_proposal(config)
    store.write_proposal(proposal)
    active_target = config.paths.state / "skills" / "parser-repair" / "SKILL.md"

    report = ProposalValidationLab(config, store).validate(proposal, _suite(tmp_path))

    assert report["status"] == "validated"
    assert report["comparison"]["score_delta"] == 1.0
    assert report["comparison"]["set_deltas"]["sealed"] == 1.0
    assert report["isolation"]["active_harness_modified"] is False
    assert not active_target.exists()
    persisted = store.find_proposal(proposal["proposal_id"])
    assert persisted["status"] == "validated"
    assert persisted["validation"]["status"] == "validated"
    assert store.find_proposal(proposal["proposal_id"], status="pending")["status"] == "validated"
    assert any(item["proposal_id"] == proposal["proposal_id"] for item in store.list_proposals(status="pending"))

    accepted = store.apply_proposal(persisted)
    assert accepted["status"] == "accepted"
    assert active_target.exists()
    assert accepted["lifecycle_events"][-1]["status"] == "accepted"

    rolled_back = store.rollback_proposal(proposal["proposal_id"])
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["lifecycle_events"][-1]["status"] == "rolled_back"


def test_equal_baseline_and_candidate_is_inconclusive(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    proposal = _skill_proposal(config)
    store.write_proposal(proposal)
    suite = _suite(tmp_path, expected_returncode=7, set_type="regression")

    report = ProposalValidationLab(config, store).validate(proposal, suite)

    assert report["status"] == "inconclusive"
    assert report["comparison"]["score_delta"] == 0.0
    with pytest.raises(PermissionError, match="validated shadow report"):
        store.apply_proposal(store.find_proposal(proposal["proposal_id"]))


def test_validated_proposal_edit_invalidates_report_and_candidate_version(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    proposal = _skill_proposal(config)
    store.write_proposal(proposal)
    ProposalValidationLab(config, store).validate(proposal, _suite(tmp_path))

    edited = ProposalService(store).edit(
        proposal["proposal_id"],
        {
            "confirm": True,
            "proposal": {
                **store.find_proposal(proposal["proposal_id"]),
                "changes": [
                    {
                        "path": "skills/parser-repair/SKILL.md",
                        "operation": "write",
                        "content": "# Edited after validation\n",
                    }
                ],
            },
        },
    )

    assert edited["status"] == "proposed"
    assert "validation" not in edited
    with pytest.raises(PermissionError, match="validated shadow report"):
        store.apply_proposal(edited)


def test_failed_validation_leaves_active_harness_unchanged(tmp_path: Path) -> None:
    config, store = _store(tmp_path)
    proposal = _skill_proposal(config)
    proposal["changes"][0]["path"] = "evals/sealed/answers.json"
    proposal["component_change"] = HarnessComponentRegistry(config).component_change_for(
        proposal["type"], proposal["changes"]
    )
    with pytest.raises(PermissionError, match="frozen outer anchor"):
        store.write_proposal(proposal)
    assert not (config.paths.state / "evals" / "sealed" / "answers.json").exists()


def test_sealed_expected_outputs_cannot_be_owned_by_composer(tmp_path: Path) -> None:
    path = tmp_path / "bad-suite.json"
    path.write_text(
        json.dumps(
            {
                "expected_owner": "proposal_composer",
                "cases": [
                    {
                        "name": "sealed",
                        "set_type": "sealed",
                        "input": {"command": ["python", "-m", "unittest"]},
                        "expected": {"returncode": 0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot be owned by proposal_composer"):
        EvalSuite.load(path)


def test_cli_lists_components_and_validates_proposal(tmp_path: Path, capsys) -> None:
    config, store = _store(tmp_path)
    proposal = _skill_proposal(config)
    store.write_proposal(proposal)
    suite = _suite(tmp_path)

    assert main(["--project", str(tmp_path), "harness", "components", "--json"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert len(manifest["components"]) == 9

    assert main(
        ["--project", str(tmp_path), "proposal", "validate", proposal["proposal_id"], "--suite", str(suite.path), "--json"]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "validated"
    assert report["comparison"]["dimensions"] == {
        "task_success": 1.0,
        "safety": 1.0,
        "regressions": None,
        "cost": 0.0,
        "latency_ms": pytest.approx(report["comparison"]["dimensions"]["latency_ms"]),
        "human_review_burden": 0.0,
    }
