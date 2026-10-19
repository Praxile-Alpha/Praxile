from praxile.eval.v2.cycle_usage import cycle_usage
from praxile.trajectory import TrajectoryLogger


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
