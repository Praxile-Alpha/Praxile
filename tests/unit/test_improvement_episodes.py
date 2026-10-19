from types import SimpleNamespace

import pytest

from praxile.eval.v2.improvement import improvement_episodes


@pytest.mark.parametrize("transition,verdict", [("improved", "helped"), ("regressed", "hurt"),
                                             ("unchanged", "no_observed_effect")])
def test_observed_verdict_never_grants_causal_promotion(transition, verdict):
    candidate = SimpleNamespace(candidate_id="candidate", digest="sha256:test", candidate_type="context_policy",
                                expected_effect={"hypothesis": "fewer repeated searches"})
    a = {"tasks": [{"task_id": "one", "trace_id": "a", "evaluator": {"resolved": False},
                    "metrics": {"tool_calls": 4, "cost": 1}}]}
    b = {"tasks": [{"task_id": "one", "trace_id": "b", "evaluator": {"resolved": True},
                    "context_activation": {"injected_context_items": ["candidate"]},
                    "metrics": {"tool_calls": 3, "cost": 2}}]}
    comparison = {"task_results": [{"task_id": "one", "transition": transition}], "cost_comparable": False}
    if transition == "regressed":
        a["tasks"][0]["evaluator"]["resolved"] = True
        b["tasks"][0]["evaluator"]["resolved"] = False
    elif transition == "unchanged":
        a["tasks"][0]["evaluator"]["resolved"] = True
    episode = improvement_episodes("exp", candidate, a, b, comparison, {"valid": True})[0]
    assert episode["verdict"] == verdict
    assert episode["observed_difference"]["measured_deltas"] == {"tool_calls": -1}
    assert not episode["promotion_eligible"]
    assert not episode["prediction_verified"]
    assert episode["experience_entry"] == "NO_ENTRY"
    b["tasks"][0]["context_activation"] = {"status": "abstained"}
    episode = improvement_episodes("exp", candidate, a, b, comparison, {"valid": True})[0]
    assert episode["verdict"] == "confounded"
    assert "candidate exposure not established" in episode["confounders"]


def test_missing_outcomes_traces_and_invariants_remain_unknown():
    candidate = SimpleNamespace(candidate_id="candidate", digest="sha256:test", candidate_type="context_policy",
                                expected_effect={"hypothesis": "test"})
    report = {"tasks": [{"task_id": "one"}]}
    episode = improvement_episodes("exp", candidate, report, report,
        {"task_results": [{"task_id": "one"}]}, {})[0]
    assert episode["verdict"] == "confounded"
    assert len(episode["confounders"]) == 4
    assert episode["observed_difference"]["measured_deltas"] == {}
    assert episode["observed_difference"]["baseline_resolved"] is None
