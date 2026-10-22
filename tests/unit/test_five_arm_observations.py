from copy import deepcopy

import pytest

from praxile.eval.five_arm_observations import observe_worker


def fixture():
    identity = {"candidate_digest": "sha256:" + "a" * 64}
    arm = {"experience_enabled": True, "task_local_enabled": True}
    worker = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "development_candidate",
        "development_context": {**identity, "activation": {"activated": True}, "injected": True},
        "trajectory": {"experimental_context": {"injected": True, "content": "PRIVATE"},
            "actions": [{"action_type": "subtask_note", "status": "success", "observation": {"status": "success"}},
                        {"action_type": "subtask_reflect", "status": "blocked", "observation": {"status": "blocked"}}]}}
    return worker, arm, identity


def test_counts_observed_use_not_just_configured_switch():
    worker, arm, identity = fixture()
    result = observe_worker(worker, arm, identity)
    assert result["activation_observed"] is True
    assert result["injection_observed"] is True
    assert result["task_local_actions"]["subtask_note"]["success"] == 1
    assert result["task_local_actions"]["subtask_reflect"]["blocked"] == 1
    assert result["configured_task_local_without_observed_actions"] is False
    assert result["causal_benefit_verified"] is False
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("actions", [None, []])
def test_absent_actions_are_unknown_not_zero(actions):
    worker, arm, identity = fixture()
    if actions is None:
        worker["trajectory"].pop("actions")
    else:
        worker["trajectory"]["actions"] = actions
    result = observe_worker(worker, arm, identity)
    if actions is None:
        assert result["task_local_actions"] is None
        assert result["configured_task_local_without_observed_actions"] is None
    else:
        assert result["configured_task_local_without_observed_actions"] is True


def test_abstained_candidate_is_not_injected():
    worker, arm, identity = fixture()
    worker["development_context"]["activation"]["activated"] = False
    worker["development_context"]["injected"] = False
    worker["trajectory"].pop("experimental_context")
    result = observe_worker(worker, arm, identity)
    assert result["activation_observed"] is False
    assert result["injection_observed"] is False


@pytest.mark.parametrize("defect", ["candidate", "activation", "injection", "runtime_injection", "mode",
                                   "disabled_experience", "disabled_memory", "status", "actions", "worker_switch", "runtime_switch"])
def test_contradictory_mechanism_evidence_rejected(defect):
    worker, arm, identity = fixture()
    if defect == "candidate":
        worker["development_context"]["candidate_digest"] = "changed"
    elif defect == "activation":
        worker["development_context"]["activation"]["activated"] = False
    elif defect == "injection":
        worker["development_context"]["injected"] = 1
    elif defect == "runtime_injection":
        worker["trajectory"]["experimental_context"]["injected"] = False
    elif defect == "mode":
        worker["experience_mode"] = "fresh_disabled"
    elif defect == "disabled_experience":
        arm["experience_enabled"] = False
    elif defect == "disabled_memory":
        arm["task_local_enabled"] = False
    elif defect == "status":
        worker["trajectory"]["actions"][0]["observation"]["status"] = "failure"
    elif defect == "worker_switch":
        worker["task_local_memory_enabled"] = False
    elif defect == "runtime_switch":
        worker["trajectory"]["task_local_policy"] = {"memory_enabled": False}
    else:
        worker["trajectory"]["actions"] = [None]
    with pytest.raises(ValueError):
        observe_worker(worker, arm, identity)


def test_disabled_memory_can_record_blocked_requests():
    worker, arm, identity = fixture()
    arm["task_local_enabled"] = False
    worker["trajectory"]["actions"] = [deepcopy(worker["trajectory"]["actions"][1])]
    result = observe_worker(worker, arm, identity)
    assert result["task_local_actions"]["subtask_reflect"]["blocked"] == 1


def test_old_worker_record_does_not_invent_mechanism_observations():
    worker, arm, identity = fixture()
    result = observe_worker({"schema_version": worker["schema_version"]}, arm, identity)
    assert result["activation_observed"] is None
    assert result["injection_observed"] is None
    assert result["task_local_actions"] is None
