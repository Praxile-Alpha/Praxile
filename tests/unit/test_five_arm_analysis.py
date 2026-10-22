from copy import deepcopy
import math

import pytest

from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan, iter_schedule, plan_digest
from praxile.eval.five_arm_analysis import analyze_study
from praxile.eval.v2.schema import EvalSchemaError


def fixture():
    plan = compile_plan({"study_id": "study", "task_ids": ["a", "b"], "heldout_task_ids": ["hold"],
        "repetitions": 3, "repeat_attempts": 2, "arm_order": list(ARMS),
        "identities": {key: "sha256:" + "a" * 64 for key in IDENTITIES},
        "budget": {"total_tokens": 100, "model_calls": 10, "wall_seconds": 10}})
    records = [{"cell_id": row["cell_id"], "plan_digest": plan_digest(plan),
                "source_sha256": "sha256:" + "b" * 64, "status": "completed", "maximum": 10,
                "score": 5 if row["arm"] == "frozen_executor" else 7}
               for row in iter_schedule(plan)]
    return plan, records


def test_repetitions_are_nested_and_four_comparisons_are_familywise_adjusted():
    plan, records = fixture()
    report = analyze_study(plan, records)
    assert report["status"] == "complete_declared_results"
    assert report["reported_cells"] == 30
    assert report["complete_task_count"] == 2
    assert report["promotion_eligible"] is False
    for result in report["comparisons"].values():
        assert result["sample_count"] == 2
        assert result["descriptive_delta"] == pytest.approx(0.2)
        margin = 2 * math.sqrt(math.log(160) / 4)
        assert result["familywise_ci95"] == [max(-1, 0.2 - margin), min(1, 0.2 + margin)]


def test_missing_cell_excludes_task_from_every_arm_and_suppresses_intervals():
    plan, records = fixture()
    missing = records.pop()
    report = analyze_study(plan, records)
    assert report["status"] == "incomplete"
    assert report["tasks"][1]["missing_cells"] == [missing["cell_id"]]
    assert report["tasks"][1]["arm_means"] == {}
    for comparison in report["comparisons"].values():
        assert comparison["sample_count"] == 1
        assert comparison["familywise_ci95"] is None


def test_failed_cell_is_not_silently_zero_scored():
    plan, records = fixture()
    records[0].update(status="failed", score=None)
    report = analyze_study(plan, records)
    assert report["complete_task_count"] == 1
    assert report["tasks"][0]["unfinished_cells"] == [records[0]["cell_id"]]


@pytest.mark.parametrize("field,value", [("score", True), ("score", float("nan")), ("score", 11),
    ("maximum", 0), ("maximum", float("inf")), ("maximum", 11),
    ("plan_digest", "sha256:" + "c" * 64), ("cell_id", "heldout"), ("status", "passed"),
    ("source_sha256", "latest")])
def test_invalid_or_confounded_records_are_rejected(field, value):
    plan, records = fixture()
    records[0][field] = value
    with pytest.raises(EvalSchemaError):
        analyze_study(plan, records)


def test_duplicate_cells_rejected_and_input_unchanged():
    plan, records = fixture()
    original = deepcopy(records)
    analyze_study(plan, records)
    assert records == original
    with pytest.raises(EvalSchemaError):
        analyze_study(plan, records + [records[0]])


def test_empty_results_remain_incomplete_not_zero_score():
    plan, _ = fixture()
    report = analyze_study(plan, [])
    assert report["complete_task_count"] == 0
    assert report["status"] == "incomplete"
    assert all(row["descriptive_delta"] is None for row in report["comparisons"].values())
