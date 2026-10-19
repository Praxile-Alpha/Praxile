from __future__ import annotations

from copy import deepcopy

from praxile.eval.v2 import compare_ab_reports
from praxile.trace import EventStore
from praxile.config import Config


def _task(task_id: str, resolved: bool, *, tokens: int, tools: int) -> dict:
    return {
        "task_id": task_id,
        "trace_id": None,
        "evaluator": {"resolved": resolved},
        "metrics": {
            "tokens": {"input": tokens, "output": 0, "cache": 0},
            "tool_calls": tools,
            "latency_ms": 10,
            "cost": 0.1,
            "cost_complete": True,
        },
    }


def test_comparison_reports_category_gain_and_regression_first_decision(tmp_path) -> None:
    baseline = {"tasks": [_task("a", False, tokens=10, tools=2), _task("b", True, tokens=10, tools=2)]}
    candidate = {"tasks": [_task("a", True, tokens=8, tools=1), _task("b", False, tokens=8, tools=1)]}
    candidate["tasks"][0]["metrics"]["progress"] = {"patch_created": True, "environment_probe_count": 2}
    candidate["tasks"][1]["metrics"]["progress"] = {"patch_created": False, "environment_probe_count": 5}
    diagnoses = {
        "baseline": [
            {"task_id": "a", "attribution": {"category": "CONTEXT"}},
            {"task_id": "b", "attribution": {"category": "UNKNOWN"}},
        ],
        "candidate": [],
    }

    result = compare_ab_reports(
        baseline,
        candidate,
        diagnoses=diagnoses,
        event_store=EventStore(Config.load(tmp_path).paths),
        cost_comparable=True,
    )

    assert result["decision"] == "regress"
    assert result["by_failure_category"]["CONTEXT"]["gains"] == 1
    assert result["by_failure_category"]["UNKNOWN"]["regressions"] == 1
    assert result["by_failure_category"]["CONTEXT"]["progress"]["candidate_patches_created"] == 1
    assert result["by_failure_category"]["UNKNOWN"]["progress"]["candidate_patches_created"] == 0
    assert result["by_failure_category"]["CONTEXT"]["progress"]["environment_probe_delta"] == 2
    assert result["by_failure_category"]["UNKNOWN"]["progress"]["environment_probe_delta"] == 5


def test_comparison_accepts_strict_efficiency_improvement_without_success_change(tmp_path) -> None:
    baseline = {"tasks": [_task("a", True, tokens=10, tools=2)]}
    candidate = deepcopy(baseline)
    candidate["tasks"][0]["metrics"]["tokens"]["input"] = 8
    candidate["tasks"][0]["metrics"]["tool_calls"] = 1

    result = compare_ab_reports(
        baseline,
        candidate,
        diagnoses={"baseline": [], "candidate": []},
        event_store=EventStore(Config.load(tmp_path).paths),
        cost_comparable=False,
    )

    assert result["decision"] == "improve"
    assert result["totals"]["cost_delta"] is None


def test_comparison_does_not_report_cost_delta_when_an_arm_is_unpriced(tmp_path):
    baseline = {"tasks": [_task("a", True, tokens=10, tools=2)]}
    candidate = deepcopy(baseline)
    candidate["tasks"][0]["metrics"]["cost_complete"] = False
    result = compare_ab_reports(baseline, candidate, diagnoses={},
        event_store=EventStore(Config.load(tmp_path).paths), cost_comparable=True)
    assert not result["cost_comparable"]
    assert result["totals"]["cost_delta"] is None
    assert result["task_results"][0]["cost_delta"] is None
