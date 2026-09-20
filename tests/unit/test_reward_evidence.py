from __future__ import annotations

import json
from pathlib import Path

from praxile.config import Config
from praxile.judge_calibration import JudgeCalibrationRunner
from praxile.reward import RewardEngine
from praxile.store import ExperienceStore


def test_reward_profile_and_material_claims_are_frozen_and_traceable(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    config.data["reward"]["profiles"]["task_classes"] = {"ui": "ux"}
    config.data["reward"]["profiles"]["definitions"]["ux"] = {
        "version": "2",
        "overrides": {"min_experience_value_for_proposals": 0.9},
    }
    trajectory = {
        "task_id": "task_reward_graph",
        "task_analysis": {"task_type": "ui", "ui_human_review_required": True},
        "result": {"status": "completed"},
        "actions": [
            {"action_type": "edit_file", "status": "success", "input": {"path": "ui.py"}},
            {
                "action_type": "browser_screenshot",
                "status": "success",
                "observation": {"data": {"artifact_type": "screenshot", "artifact": ".praxile/experience/artifacts/ui.png"}},
            },
        ],
        "diff_summary": {"files_changed": ["ui.py"], "insertions": 2, "deletions": 1},
        "cost": {"tool_calls": 1, "model_calls": 1},
    }

    report = RewardEngine(config).build_report(trajectory, [{"status": "success", "data": {"command": "pytest"}}])

    assert report["schema_version"] == 2
    assert report["reward_profile"]["name"] == "ux"
    assert report["reward_profile"]["effective_policy"]["min_experience_score"] == 0.9
    assert report["evidence_graph"]["coverage"] == 1.0
    assert any(item["type"] == "screenshot" for item in report["evidence_graph"]["nodes"])
    assert {item["provenance"] for item in report["evidence_graph"]["nodes"]} <= {
        "objective", "derived", "llm_assisted", "human_confirmed"
    }
    assert report["escalation"]["required"] is True
    assert "ux_risk" in report["escalation"]["reasons"]


def test_reward_claims_are_indexed_and_added_to_experience_graph(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    trajectory = {
        "task_id": "task_index_reward",
        "start_time": "2026-08-12T00:00:00+00:00",
        "end_time": "2026-08-12T00:00:01+00:00",
        "user_task": "Fix test",
        "result": {"status": "completed", "summary": "done"},
        "actions": [],
        "cost": {},
    }
    trajectory["reward_report"] = RewardEngine(config).build_report(trajectory, [{"status": "success"}])
    store.record_trajectory(trajectory)

    indexed = store.reward_evidence_for_task("task_index_reward")
    assert indexed["found"] is True
    assert indexed["reward_profile"]["profile_version"]
    graph = store.rebuild_experience_graph()
    assert graph["relation_counts"]["supports_reward_claim"] >= 1
    assert graph["relation_counts"]["contributes_to_reward"] == 6


def test_controlled_judge_calibration_and_gated_policy_proposal(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    config.data["semantic_judges"]["calibration"]["repeated_runs"] = 2
    suite = tmp_path / "judge-suite.json"
    suite.write_text(
        json.dumps(
            {
                "judge": "reward_guard",
                "known_labels": ["failed_result", "failed_test", "architecture_risk"],
                "cases": [
                    {
                        "case_id": "failure",
                        "trajectory": {"result": {"status": "completed"}},
                        "mutations": [{"op": "set", "path": "result.status", "value": "failed"}],
                        "expected_detections": ["failed_result", "failed_test"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = JudgeCalibrationRunner(config)
    first = runner.run(suite)
    second = runner.run(suite)
    proposal = runner.reward_policy_proposal([first, second])

    assert first["confusion"]["true_positive"] == 1
    assert first["confusion"]["false_negative"] == 1
    assert first["recall"] == 0.5
    assert first["evidence_coverage"] == 0.5
    assert first["calibration_error"] > 0
    assert first["false_promotion_rate"] == 0.0
    assert proposal is not None
    assert proposal["type"] == "reward_policy"
    assert proposal["architecture_gate_required"] is True
    assert proposal["requires_human_approval"] is True
