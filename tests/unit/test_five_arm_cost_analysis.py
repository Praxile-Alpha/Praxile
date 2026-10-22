import pytest

from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan, iter_schedule
from praxile.eval.five_arm_cost_analysis import compare_usage


def fixture():
    plan = compile_plan({"study_id": "cost", "task_ids": ["a", "b"], "heldout_task_ids": ["hidden"],
        "repetitions": 2, "repeat_attempts": 2, "identities": {key: "sha256:" + "a" * 64 for key in IDENTITIES},
        "budget": {"total_tokens": 1000, "model_calls": 10, "wall_seconds": 60}, "arm_order": list(ARMS)})
    cells = {}
    for row in iter_schedule(plan):
        base = 100 if row["task_id"] == "a" else 10000
        delta = (10 if row["task_id"] == "a" else -30) * (row["repetition"] + 1)
        value = base + (delta if row["arm"] != "frozen_executor" else 0)
        cells[row["cell_id"]] = {"total_tokens": {"input": value, "output": 0}}
    return plan, cells


def test_repeats_are_nested_in_tasks_and_token_volume_does_not_weight_tasks():
    plan, cells = fixture()
    result = compare_usage(plan, cells)
    assert result["complete_task_count"] == 2
    for item in result["comparisons"].values():
        assert item["sample_count"] == 2  # Not four independent repetitions.
        assert item["token_delta"] == {"input": -15, "output": 0}
        assert item["confidence_interval"] is None
    assert result["cost_usd"] is None
    assert result["promotion_eligible"] is False


@pytest.mark.parametrize("defect", ["missing", "input", "output"])
def test_missing_receipt_excludes_whole_task_from_all_comparisons(defect):
    plan, cells = fixture()
    cell = next(row for row in iter_schedule(plan) if row["task_id"] == "b" and row["arm"] == "full_praxile")
    if defect == "missing":
        cells.pop(cell["cell_id"])
    else:
        cells[cell["cell_id"]]["total_tokens"][defect] = None
    result = compare_usage(plan, cells)
    assert result["complete_task_count"] == 1
    for item in result["comparisons"].values():
        assert item["sample_count"] == 1
        assert item["token_delta"] == {"input": 15, "output": 0}
    assert result["tasks"][1]["arm_means"] == {}


def test_no_receipts_are_not_zero_cost():
    plan, _ = fixture()
    result = compare_usage(plan, {})
    assert result["complete_task_count"] == 0
    assert all(row["token_delta"] == {"input": None, "output": None} for row in result["comparisons"].values())


@pytest.mark.parametrize("invalid", [-1, True, "10", 0.5, float("inf"), 2**54])
def test_invalid_token_counts_rejected(invalid):
    plan, cells = fixture()
    next(iter(cells.values()))["total_tokens"]["input"] = invalid
    with pytest.raises(ValueError):
        compare_usage(plan, cells)


def test_undeclared_cell_rejected():
    plan, cells = fixture()
    cells["hidden"] = {"total_tokens": {"input": 0, "output": 0}}
    with pytest.raises(ValueError):
        compare_usage(plan, cells)
