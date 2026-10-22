from praxile.eval.v2.cycle_usage import cycle_usage
from praxile.trajectory import TrajectoryLogger
from praxile.eval.v2.cycle_usage import CycleUsageCheckpoint
from praxile.utils import read_json, write_json
import pytest


def test_complete_recorded_arms_are_not_whole_experiment_cost():
    arm = {"metrics": {"tokens": {"input": 10, "output": 2, "cache": 0},
                       "tokens_complete": True, "cost_complete": True, "cost_total": 0.25}}
    result = cycle_usage([{"baseline": arm, "candidate": arm}], composer_requested=False)
    assert result["tokens"]["input"]["complete_total"] == 20
    assert result["recorded_scope_cost_total"] == 0.5
    assert result["whole_experiment_cost_total"] is None
    assert result["whole_experiment_complete"] is False


def test_composer_partial_usage_is_not_free_or_complete():
    logger = TrajectoryLogger("compose", {})
    logger.add_model_cost({"prompt_tokens": 7})
    result = cycle_usage([], composer_requested=True, composer_trajectory=logger.data)
    assert result["tokens"]["input"]["known_subtotal"] == 7
    assert result["tokens"]["input"]["complete_total"] is None
    assert result["components"][0]["usage_evidence"]["complete_tokens"]["completion_tokens"] is None
    assert result["recorded_scope_cost_total"] is None


def test_missing_components_never_sum_to_complete_zero():
    result = cycle_usage([{}], composer_requested=True)
    assert result["component_count"] == 3
    assert result["tokens"]["input"]["complete_total"] is None
    assert result["recorded_scope_cost_total"] is None


def test_partially_priced_arm_retains_known_cost():
    arm = {"metrics": {"cost_complete": False, "reported_cost_subtotal": 0.4}}
    result = cycle_usage([{"baseline": arm}], composer_requested=False)
    assert result["known_cost_subtotal"] == 0.4
    assert result["recorded_scope_cost_total"] is None


@pytest.mark.parametrize("usage,reported", [({}, 0), ({"prompt_tokens": 0}, 1)])
def test_composer_missing_tokens_are_distinct_from_explicit_zero(usage, reported):
    logger = TrajectoryLogger("compose", {})
    logger.add_model_cost(usage, status="error")
    result = cycle_usage([], composer_requested=True, composer_trajectory=logger.data)
    assert result["tokens"]["input"]["reported_components"] == reported
    assert result["components"][0]["known_tokens"]["input"] == (0 if reported else None)
    assert result["tokens"]["output"]["reported_components"] == 0
    assert result["tokens"]["input"]["complete_total"] is None


def test_checkpoint_retains_completed_cost_when_next_operation_interrupts(tmp_path):
    arm = {"metrics": {"tokens": {"input": 10, "output": 2, "cache": 0},
                       "tokens_complete": True, "cost_complete": True, "cost_total": 0.25}}
    reports = []
    ledger = CycleUsageCheckpoint(tmp_path, reports, composer_requested=False)
    with ledger.operation("first"):
        pending = read_json(tmp_path / "usage.json")
        assert pending["pending_operation"] == "first"
        reports.append({"experiment_id": "first", "baseline": arm, "candidate": arm})
    with pytest.raises(KeyboardInterrupt):
        with ledger.operation("second"):
            raise KeyboardInterrupt()
    saved = read_json(tmp_path / "usage.json")
    assert saved["checkpoint_status"] == "outcome_unknown"
    assert saved["pending_operation"] == "second"
    assert saved["completed_experiment_ids"] == ["first"]
    assert saved["known_cost_subtotal"] == 0.5
    assert saved["whole_experiment_cost_total"] is None
    assert "unfinished operation: second" in saved["unaccounted_scope"]


def test_composer_failure_flushes_available_usage_without_claiming_completion(tmp_path):
    ledger = CycleUsageCheckpoint(tmp_path, [], composer_requested=True)
    with pytest.raises(ValueError, match="invalid response"):
        with ledger.operation("proposal_composer"):
            logger = TrajectoryLogger("compose", {})
            logger.add_model_cost({"prompt_tokens": 19})
            write_json(tmp_path / "composer-trajectory.json", logger.data)
            raise ValueError("invalid response")
    saved = read_json(tmp_path / "usage.json")
    assert saved["checkpoint_status"] == "outcome_unknown"
    assert saved["tokens"]["input"]["known_subtotal"] == 19
    assert saved["tokens"]["output"]["reported_components"] == 0
    assert saved["whole_experiment_complete"] is False
