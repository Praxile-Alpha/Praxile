from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.evolution import EvolutionEngine
from praxile.judge_governance import JudgeGovernance
from praxile.store import ExperienceStore


def _trajectory(task_id: str, *, judge_score: float | None, tests_passed: bool | None) -> dict:
    tests_run = tests_passed is not None
    trajectory = {
        "task_id": task_id,
        "reward_report": {
            "objective_score_component": 0.9 if tests_passed else 0.3,
            "objective_signals": {
                "tests_run": tests_run,
                "tests_passed": tests_passed,
                "regression_status": "passed" if tests_passed else "failed" if tests_run else "no_tests_available",
                "blocked_actions": 0,
                "failed_actions": 0,
                "spec_compliance_status": None,
            },
        },
    }
    if judge_score is not None:
        trajectory["llm_judge_reward"] = {
            "active": True,
            "score": judge_score,
            "recommended_action": "accept",
            "model_role": "reward_judge",
        }
    return trajectory


def test_self_judgment_and_verifier_are_separate_and_calibrated(tmp_path: Path) -> None:
    governance = JudgeGovernance(Config.load(tmp_path))
    observation = governance.observe(_trajectory("task-1", judge_score=0.9, tests_passed=False))

    assert observation["self_judgment"]["score"] == 0.9
    assert observation["verifier_outcome"]["score"] == 0.3
    assert observation["verifier_outcome"]["passed"] is False
    assert observation["judgment_calibration"]["false_positive"] is True
    assert observation["judgment_calibration"]["false_promotion"] is True
    assert observation["judgment_calibration"]["absolute_error"] == 0.6
    assert observation["judgment_calibration"]["promotion_eligible"] is False


def test_self_judgment_without_verifier_cannot_grant_promotion(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    governance = JudgeGovernance(config)
    observation = governance.observe(_trajectory("task-2", judge_score=0.95, tests_passed=None))
    trajectory = {
        **_trajectory("task-2", judge_score=0.95, tests_passed=None),
        **{key: observation[key] for key in ("self_judgment", "verifier_outcome", "judgment_calibration")},
    }
    proposal = {
        "source_task_id": "task-2",
        "type": "memory_update",
        "confidence": 0.9,
        "evidence_summary": ["LLM believes the change is useful."],
        "applicability_scope": "Similar tasks.",
        "anti_scope": ["Unrelated tasks."],
        "target_files": ["memory/project.md"],
    }

    gate = EvolutionEngine(config)._proposal_gate_decision(proposal, trajectory)

    assert gate["passed"] is True
    assert gate["promotion_eligibility"]["eligible"] is False
    assert gate["promotion_eligibility"]["self_judgment_only"] is True
    assert gate["promotion_eligibility"]["basis"] == "insufficient_verifier_evidence"

    config.data["proposal_gate"]["enabled"] = False
    generated = EvolutionEngine(config)._apply_proposal_gate([proposal], trajectory)
    assert generated[0]["promotion_eligibility"]["eligible"] is False


def test_sqlite_metrics_and_transfer_effect_are_persisted(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    governance = JudgeGovernance(config)
    source = governance.observe(_trajectory("source", judge_score=0.8, tests_passed=False))
    current = governance.observe(_trajectory("next", judge_score=0.85, tests_passed=True))
    store.record_judge_observation(source)

    transfer = governance.transfer_observation(
        source,
        current,
        current_task_id="next",
        asset_paths=["memory/project.md"],
    )
    assert transfer is not None
    store.record_judge_observation(governance.apply_transfer(source, transfer))
    store.record_judge_observation(current)

    persisted = store.get_judge_observation("source")
    assert persisted is not None
    assert persisted["next_task_delta"]["status"] == "observed"
    assert persisted["transfer_effect"]["effect"] == "positive"
    assert persisted["transfer_effect"]["causal_claim"] is False
    metrics = JudgeGovernance.metrics(store.list_judge_observations())
    assert metrics["precision"] == 0.5
    assert metrics["false_promotion_rate"] == 0.5
    assert metrics["calibration_error"] == 0.275
