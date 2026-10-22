from copy import deepcopy
import hashlib

import pytest

from praxile.trace import native_run_to_events, v1_trajectory_to_events


def result(activated=True, injected=True):
    trajectory = {"task_id": "native", "start_time": "2026-10-19T00:00:00Z", "result": {"status": "completed"}}
    if activated:
        trajectory["experimental_context"] = {"content": "PRIVATE SUGGESTION", "injected": injected,
            "sha256": hashlib.sha256(b"PRIVATE SUGGESTION").hexdigest()}
    return {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "development_candidate",
        "trajectory": trajectory, "development_context": {
            "schema_version": "praxile.native_development_context.v1", "track": "development",
            "activation": {"activated": activated}, "injected": injected,
            "candidate_digest": "sha256:" + "a" * 64, "task_body_sha256": "b" * 64,
            "promotion_eligible": False, "permission_granted": False}}


@pytest.mark.parametrize("active,injected", [(True, True), (True, False), (False, False)])
def test_native_activation_and_injection_are_separate_and_deterministic(active, injected):
    value = result(active, injected)
    original = deepcopy(value)
    events = native_run_to_events(value)
    assert value == original
    activation = [e for e in events if e.type == "CONTEXT_ACTIVATION"]
    injection = [e for e in events if e.type == "CONTEXT_INJECT"]
    assert len(activation) == 1 and len(injection) == int(injected)
    if injected:
        assert injection[0].evidence_refs == ("event:" + activation[0].event_id,)
    assert "PRIVATE SUGGESTION" not in str([e.payload for e in activation + injection])
    assert [e.event_id for e in events] == [e.event_id for e in native_run_to_events(value)]


def test_legacy_baseline_event_ids_are_unchanged():
    value = result(False, False)
    value.pop("development_context")
    value["experience_mode"] = "fresh_disabled"
    assert native_run_to_events(value) == v1_trajectory_to_events(value["trajectory"])


def test_heldout_context_is_not_projected_as_development():
    value = result()
    value["experience_mode"] = "heldout_frozen_candidate"
    with pytest.raises(ValueError, match="activation"):
        native_run_to_events(value)
    value["development_context"]["track"] = "heldout_frozen"
    events = native_run_to_events(value)
    assert {e.payload["track"] for e in events if e.type in {"CONTEXT_ACTIVATION", "CONTEXT_INJECT"}} == {"heldout_frozen"}


def test_forged_injection_or_digest_rejected():
    value = result()
    value["trajectory"]["experimental_context"]["injected"] = False
    with pytest.raises(ValueError, match="injection evidence"):
        native_run_to_events(value)
    value = result()
    value["trajectory"]["experimental_context"]["sha256"] = "forged"
    with pytest.raises(ValueError, match="digest"):
        native_run_to_events(value)
