from copy import deepcopy
import hashlib

import pytest

from praxile.eval.online_probe_exposure import observe_probe_exposure


def fixture():
    candidate = {"context_item": {"content": "PRIVATE advice"}}
    identity = {"candidate_digest": "sha256:" + "a" * 64, "track": "development"}
    worker = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "development_candidate",
        "development_context": {**identity, "activation": {"activated": True}, "injected": True},
        "trajectory": {"experimental_context": {"content": "PRIVATE advice", "injected": True,
            "sha256": hashlib.sha256(b"PRIVATE advice").hexdigest()}, "actions": []}}
    return worker, {"development_context_identity": deepcopy(identity)}, candidate, identity


def test_bound_injection_is_not_remote_consumption_or_benefit():
    worker, execution, candidate, identity = fixture()
    result = observe_probe_exposure(worker, execution, candidate=candidate, identity=identity, task_local_enabled=False)
    assert result["candidate_exposure_verified"] is True
    assert result["remote_consumption_verified"] is False
    assert result["causal_benefit_verified"] is False
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("defect", ["identity", "content", "hash", "worker_digest", "activation", "runtime_flag", "baseline"])
def test_conflicting_exposure_rejected(defect):
    worker, execution, candidate, identity = fixture()
    if defect == "identity":
        execution["development_context_identity"] = {"candidate_digest": "other"}
    elif defect == "content":
        worker["trajectory"]["experimental_context"]["content"] = "different"
    elif defect == "hash":
        worker["trajectory"]["experimental_context"]["sha256"] = "different"
    elif defect == "worker_digest":
        worker["development_context"]["candidate_digest"] = "different"
    elif defect == "activation":
        worker["development_context"]["activation"]["activated"] = False
    elif defect == "runtime_flag":
        worker["trajectory"]["experimental_context"]["injected"] = False
    else:
        candidate, identity = None, None
    with pytest.raises(ValueError):
        observe_probe_exposure(worker, execution, candidate=candidate, identity=identity, task_local_enabled=False)


@pytest.mark.parametrize("missing", ["runtime", "content", "hash", "identity", "context"])
def test_missing_evidence_is_not_verified(missing):
    worker, execution, candidate, identity = fixture()
    if missing == "runtime":
        worker["trajectory"].pop("experimental_context")
    elif missing in ("content", "hash"):
        worker["trajectory"]["experimental_context"].pop("sha256" if missing == "hash" else "content")
    elif missing == "identity":
        execution.clear()
    else:
        worker.pop("development_context")
        worker["trajectory"].pop("experimental_context")
    result = observe_probe_exposure(worker, execution, candidate=candidate, identity=identity, task_local_enabled=False)
    assert result["candidate_exposure_verified"] is False


def test_scope_abstention_is_not_failure_or_exposure():
    worker, execution, candidate, identity = fixture()
    worker["development_context"].update(activation={"activated": False}, injected=False)
    worker["trajectory"].pop("experimental_context")
    result = observe_probe_exposure(worker, execution, candidate=candidate, identity=identity, task_local_enabled=False)
    assert result["activation_observed"] is False
    assert result["candidate_exposure_verified"] is False
