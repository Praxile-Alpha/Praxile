"""Bound runtime injection observations, not remote consumption or causal proof."""
import hashlib

from .five_arm_observations import observe_worker


def observe_probe_exposure(worker, execution, *, candidate, identity, task_local_enabled):
    declared = execution.get("development_context_identity")
    if declared is not None and declared != identity:
        raise ValueError("probe execution candidate identity mismatch")
    observations = observe_worker(worker, {"experience_enabled": candidate is not None,
        "task_local_enabled": task_local_enabled}, identity)
    result = {**observations, "candidate_exposure_verified": False,
              "scope": "bound_runtime_prompt_construction", "remote_consumption_verified": False}
    if candidate is None:
        result["status"] = "baseline_disabled" if worker.get("experience_mode") == "fresh_disabled" else "unknown"
        return result
    runtime = worker.get("trajectory", {}).get("experimental_context")
    content = candidate["context_item"]["content"]
    digest = hashlib.sha256(content.encode()).hexdigest()
    if runtime is not None:
        if (("content" in runtime and runtime["content"] != content)
                or ("sha256" in runtime and runtime["sha256"] != digest)):
            raise ValueError("runtime injected content differs from frozen candidate")
    complete = (declared == identity and worker.get("experience_mode") == "development_candidate"
        and observations["activation_observed"] is True and observations["injection_observed"] is True
        and isinstance(runtime, dict) and runtime.get("injected") is True
        and runtime.get("content") == content and runtime.get("sha256") == digest)
    result.update(candidate_exposure_verified=complete,
                  status="bound_runtime_injection" if complete else "not_established")
    return result
