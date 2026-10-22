"""Read bound worker mechanism observations without inferring causal benefit."""
from pathlib import PurePosixPath

from .five_arm_usage import iter_attempt_reports
from .owned_content_review import _reader, _json, _sha


def _optional_bool(mapping, key):
    if key not in mapping:
        return None
    if type(mapping[key]) is not bool:
        raise ValueError("mechanism observation must be boolean")
    return mapping[key]


def observe_worker(worker, scheduled, candidate_identity):
    if worker.get("schema_version") != "praxile.native_benchmark_run.v1":
        raise ValueError("unsupported mechanism trajectory")
    trajectory = worker.get("trajectory", {})
    if not isinstance(trajectory, dict):
        raise ValueError("invalid mechanism trajectory")
    reported_memory = _optional_bool(worker, "task_local_memory_enabled")
    if reported_memory is not None and reported_memory is not scheduled["task_local_enabled"]:
        raise ValueError("worker task-local switch differs from scheduled arm")
    policy = trajectory.get("task_local_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            raise ValueError("invalid runtime task-local policy")
        runtime_memory = _optional_bool(policy, "memory_enabled")
        if runtime_memory is not None and runtime_memory is not scheduled["task_local_enabled"]:
            raise ValueError("runtime task-local policy differs from scheduled arm")
    mode = worker.get("experience_mode")
    expected_mode = "development_candidate" if scheduled["experience_enabled"] else "fresh_disabled"
    if mode is not None and mode != expected_mode:
        raise ValueError("worker experience mode differs from scheduled arm")
    activation, injected = None, None
    context = worker.get("development_context")
    if context is not None:
        if not scheduled["experience_enabled"] or not isinstance(context, dict):
            raise ValueError("unexpected development context")
        if context.get("candidate_digest") != candidate_identity["candidate_digest"]:
            raise ValueError("worker candidate differs from scheduled candidate")
        gate = context.get("activation", {})
        if not isinstance(gate, dict):
            raise ValueError("invalid activation observation")
        activation = _optional_bool(gate, "activated")
        injected = _optional_bool(context, "injected")
    runtime_context = trajectory.get("experimental_context")
    if runtime_context is not None:
        if not isinstance(runtime_context, dict) or not scheduled["experience_enabled"]:
            raise ValueError("unexpected runtime context")
        runtime_injected = _optional_bool(runtime_context, "injected")
        if runtime_injected is not None and injected is not None and runtime_injected != injected:
            raise ValueError("worker and runtime injection disagree")
        if runtime_injected is not None:
            injected = runtime_injected
    if injected is True and activation is not True:
        raise ValueError("injection lacks positive activation evidence")
    counts = None
    actions = trajectory.get("actions")
    if actions is not None:
        if not isinstance(actions, list):
            raise ValueError("invalid mechanism actions")
        counts = {name: {"attempted": 0, "success": 0, "blocked": 0, "other": 0}
                  for name in ("subtask_note", "subtask_reflect")}
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("action_type"), str):
                raise ValueError("invalid action observation")
            name = action["action_type"]
            if name not in counts:
                continue
            status = action.get("status")
            observation = action.get("observation")
            if (not isinstance(status, str) or not isinstance(observation, dict)
                    or observation.get("status") != status):
                raise ValueError("inconsistent task-local action status")
            if not scheduled["task_local_enabled"] and status == "success":
                raise ValueError("disabled task-local mechanism reported success")
            counts[name]["attempted"] += 1
            counts[name][status if status in {"success", "blocked"} else "other"] += 1
    reflections = None
    if "subtask_reflections" in trajectory:
        records = trajectory["subtask_reflections"]
        if not isinstance(records, list):
            raise ValueError("invalid reflection outcome ledger")
        reflections = {status: 0 for status in ("requested", "note_created", "abstained", "failed")}
        for record in records:
            status = record.get("status") if isinstance(record, dict) else None
            if not isinstance(status, str) or status not in reflections:
                raise ValueError("invalid reflection outcome")
            if not scheduled["task_local_enabled"]:
                raise ValueError("disabled task-local mechanism contains reflection requests")
            reflections[status] += 1
    return {"activation_observed": activation, "injection_observed": injected,
            "reflection_outcomes": reflections,
            "task_local_actions": counts,
            "configured_task_local_without_observed_actions":
                scheduled["task_local_enabled"] and all(item["attempted"] == 0 for item in counts.values())
                if counts is not None else None,
            "causal_benefit_verified": False}


def observe_cell(directory, cell):
    read = _reader(directory)
    results = []
    for index, report, relative in iter_attempt_reports(directory, cell):
        item = {"attempt": index, "status": "unknown", "observations": None}
        results.append(item)
        if report is None or report.get("source_result_sha256") is None:
            continue
        raw = read(str(PurePosixPath(relative).parent / "agent/praxile-result.json"), max_bytes=16 * 1024**2)
        if _sha(raw) != report["source_result_sha256"]:
            raise ValueError("mechanism trajectory changed")
        item.update(status="bound_worker_record", source_sha256=_sha(raw),
                    observations=observe_worker(_json(raw), cell["cell"], cell["candidate_identity"]))
    return results
