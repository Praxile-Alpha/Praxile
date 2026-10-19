from copy import deepcopy

import pytest

from praxile.control_plane.lab import _statistics, _complete_cost
from praxile.eval.v2.schema import EvalSchemaError


def arm(repetition, tasks):
    return {"repetition": repetition, "tasks": [{"task_id": identity, "baseline_resolved": left,
        "candidate_resolved": right, "transition": "improved" if left is False and right is True else "unknown"}
        for identity, left, right in tasks],
        "efficiency": {"candidate_cost": 1, "baseline_cost": 1, "candidate_tokens": 10, "baseline_tokens": 10}}


def test_identical_repetitions_do_not_inflate_sample_count_or_shrink_interval():
    once = _statistics([arm(1, [("a", False, True), ("b", True, True)])])
    repeated = _statistics([arm(i, [("a", False, True), ("b", True, True)]) for i in range(1, 101)])
    assert once["sample_count"] == repeated["sample_count"] == 2
    assert repeated["observation_count"] == 200
    assert once["delta"] == repeated["delta"]
    assert repeated["delta"]["ci95"][0] < 0


def test_missing_one_outcome_excludes_whole_task_pair_and_reports_unknown():
    stats = _statistics([arm(1, [("a", False, True)]), arm(2, [("a", None, True)])])
    assert stats["sample_count"] == 0
    assert stats["incomplete_task_ids"] == ["a"]
    assert stats["delta"]["estimate"] is None
    assert stats["delta"]["ci95"] == [-1.0, 1.0]


def test_each_task_gets_equal_weight_after_averaging_its_repetitions():
    stats = _statistics([arm(1, [("a", False, True), ("b", True, False)]),
                         arm(2, [("a", False, True), ("b", True, True)])])
    assert stats["delta"]["estimate"] == 0.25
    assert stats["paired_task_means"][1]["delta"] == -0.5
    assert stats["efficiency"]["cost_delta_ci95"] is None


def test_duplicate_repetitions_or_task_rows_rejected():
    one = arm(1, [("a", False, True)])
    with pytest.raises(EvalSchemaError, match="repetition"):
        _statistics([one, deepcopy(one)])
    one["tasks"] *= 2
    with pytest.raises(EvalSchemaError, match="duplicate task"):
        _statistics([one])


def test_incomplete_cost_cannot_be_averaged_as_zero_or_omitted():
    first = arm(1, [("a", False, True)])
    second = arm(2, [("a", False, True)])
    second["efficiency"]["candidate_cost"] = None
    stats = _statistics([first, second])
    assert stats["efficiency"]["cost_delta_mean"] is None
    assert stats["efficiency"]["unpriced_repetition_count"] == 1
    assert _complete_cost({"metrics": {"cost": 0}}) is None
    assert _complete_cost({"metrics": {"cost_complete": True, "cost_total": 0}}) == 0
    assert _complete_cost({"metrics": {"cost_complete": True, "cost_total": float("nan")}}) is None
