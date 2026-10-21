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
    retained = episode["retained_local_evidence"]
    assert retained["disposition"] == "retained_for_review"
    assert retained["task_verdict"] == verdict
    assert retained["observations"] == [{"kind": "run_metric_delta", "metric": "tool_calls", "delta": -1,
                                          "behavior_or_quality_improvement_proven": False}]
    assert not retained["promotion_eligible"] and not retained["causal_attribution"]
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
    assert episode["retained_local_evidence"]["disposition"] == "NO_ENTRY"


def test_retained_local_evidence_is_deterministic_and_bound_to_observations():
    from praxile.eval.v2.improvement import retained_local_evidence
    episode = {"episode_id": "exp:task", "baseline": {"trace_id": "a"}, "candidate": {"trace_id": "b"},
        "verdict": "hurt", "confounders": [], "observed_difference": {"measured_deltas": {}},
        "behavior_probe": {"status": "measured", "measurement": {"kind": "repeated_exact_operations"},
            "baseline": 2, "candidate": 1, "delta": -1,
            "event_evidence": [{"baseline_event_ids": ["a1", "a2", "a3"], "candidate_event_ids": ["b1", "b2"]}],
            "verdict": "helped", "comparison_invariants_verified": True, "candidate_exposure_verified": True}}
    report = retained_local_evidence(episode)
    assert report == retained_local_evidence(episode)
    assert report["task_verdict"] == "hurt"
    assert report["observations"][0]["verdict"] == "helped"
    episode["behavior_probe"]["event_evidence"][0]["baseline_event_ids"].append("a4")
    assert retained_local_evidence(episode)["evidence_sha256"] != report["evidence_sha256"]
    assert report["observations"][0]["event_evidence"][0]["baseline_event_ids"] == ["a1", "a2", "a3"]
