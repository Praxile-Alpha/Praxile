"""Replay bound prefix probes into non-causal, evaluator-free composer feedback."""
from pathlib import Path

from ..trace.compatibility import native_run_to_events
from .contracts import BenchmarkTask
from .native_context import owned_candidate_identity
from .online_probe_exposure import observe_probe_exposure
from .online_stream import OnlineStream
from .owned_content_review import _reader, _json, _sha
from .v2.alignment import align_operations
from .v2.behavior_probe import probe_repetition
from .v2.candidate import ContextCandidate


def audit_online_probe(stream_directory, *, directory, manifest_sha256, report_sha256):
    root = Path(directory).absolute()
    read = _reader(root)
    manifest_raw, report_raw = read("manifest.json"), read("report.json", max_bytes=16 * 1024**2)
    if _sha(manifest_raw) != manifest_sha256 or _sha(report_raw) != report_sha256:
        raise ValueError("probe plan or report digest mismatch")
    plan, report = _json(manifest_raw), _json(report_raw)
    stream = OnlineStream(stream_directory)
    expected = stream.probe_snapshot(plan["task_indices"], plan["candidates"])
    if plan != expected:
        raise ValueError("probe no longer matches the frozen stream prefix")
    return _audit_probe_records(stream, root, plan, report, manifest_sha256, report_sha256,
                                arms=("baseline", "first", "second"))


def _audit_probe_records(stream, root, plan, report, manifest_sha256, report_sha256, *, arms):
    read = _reader(root)
    if len(arms) != len(plan["candidates"]) + 1 or arms[0] != "baseline":
        raise ValueError("probe audit arm schedule mismatch")
    if (report.get("schema_version") != "praxile.online_probe_report.v1"
            or report.get("status") != "executions_returned_not_scored" or report.get("track") != plan["track"]
            or report.get("source_ledger_sha256") != plan["source_ledger_sha256"]
            or not isinstance(report.get("attempts"), list) or len(report["attempts"]) != len(arms) * len(plan["tasks"])):
        raise ValueError("incomplete or mismatched probe report")
    parents = [ContextCandidate.from_dict(value) for value in plan["candidates"]]
    feedback = [{"track": "development", "source_track": plan["track"], "candidate_digest": parent.digest,
        "invariant_check": {"valid": True, "scope": "recorded_execution_conditions"},
        "improvement_episodes": [], "promotion_eligible": False} for parent in parents]
    options = plan["execution"]
    for offset, (index, value) in enumerate(zip(plan["task_indices"], plan["tasks"])):
        task = BenchmarkTask.from_dict({**value, "split": "development"})
        events = {}
        for arm_index, arm in enumerate(arms):
            row = report["attempts"][offset * len(arms) + arm_index]
            if (row.get("task_index") != index or row.get("task_id") != task.task_id or row.get("arm") != arm
                    or row.get("status") != "executor_returned"):
                raise ValueError("probe attempt order or identity mismatch")
            relative = row["report"]
            prefix = f"task-{index}/{arm}/"
            if not isinstance(relative, str) or not relative.startswith(prefix) or not relative.endswith("/execution.json"):
                raise ValueError("probe execution belongs to another arm")
            raw = read(relative, max_bytes=16 * 1024**2)
            trace_raw = read(str(Path(relative).parent / "agent/praxile-result.json"), max_bytes=16 * 1024**2)
            execution, worker = _json(raw), _json(trace_raw)
            conditions = {"task_fingerprint": task.fingerprint, "status": "executor_returned", "cleanup_verified": True,
                "image": task.environment.revision, "wheel_sha256": options["wheel_sha256"], "model": options["model"],
                "endpoint_sha256": _sha(options["base_url"].encode()), "max_steps": options.get("max_steps", 50),
                "timeout_seconds": options.get("timeout_seconds", 900),
                "model_request_options": options.get("model_request_options", {}),
                "task_local_memory_enabled": options.get("task_local_memory_enabled", False)}
            if (any(execution.get(key) != item for key, item in conditions.items())
                    or _sha(raw) != row.get("report_sha256") or _sha(trace_raw) != row.get("trajectory_sha256")
                    or execution.get("source_result_sha256") != _sha(trace_raw)):
                raise ValueError("probe conditions or bound execution evidence changed")
            identity, parent = None, None
            if arm_index:
                parent = plan["candidates"][arm_index - 1]
                parent_path = root / f"parent-{arm_index - 1}.json"
                parent_raw = read(parent_path.name)
                if _json(parent_raw) != parent:
                    raise ValueError("probe parent differs from plan")
                identity = owned_candidate_identity(task, parent_path, _sha(parent_raw), plan["repository"], development_only=True)
            exposure = observe_probe_exposure(worker, execution, candidate=parent, identity=identity,
                task_local_enabled=conditions["task_local_memory_enabled"])
            if (row.get("mechanisms") != exposure
                    or row.get("candidate_exposure_verified") != exposure["candidate_exposure_verified"]):
                raise ValueError("probe mechanism summary differs from bound trace")
            events[arm] = native_run_to_events(worker)
            if arm_index:
                alignment = align_operations(events["baseline"], events[arm])
                behavior = probe_repetition(parents[arm_index - 1].expected_effect, alignment)
                behavior["candidate_exposure_verified"] = exposure["candidate_exposure_verified"]
                if not exposure["candidate_exposure_verified"]:
                    behavior["prediction_verified"] = False
                feedback[arm_index - 1]["improvement_episodes"].append({"task_id": task.task_id,
                    "candidate": {"digest": parents[arm_index - 1].digest}, "verdict": "confounded",
                    "confounders": ["task_outcome_not_evaluated", "single_pair_no_state_equivalence"],
                    "operation_alignment": alignment, "behavior_probe": behavior, "promotion_eligible": False})
    if stream.status()["ledger_sha256"] != plan["source_ledger_sha256"]:
        raise ValueError("stream advanced during probe audit")
    return {"schema_version": "praxile.online_probe_feedback.v1", "track": plan["track"],
        "manifest_sha256": manifest_sha256, "report_sha256": report_sha256,
        "source_ledger_sha256": plan["source_ledger_sha256"], "parent_reports": feedback,
        "local_file_consistency_checked": True, "task_success_proven": False, "promotion_eligible": False}
