"""Fresh baseline/two-parent probes of completed online-prefix tasks, no judge."""
from pathlib import Path

from .contracts import BenchmarkTask
from .native_context import owned_candidate_identity
from .online_stream import OnlineStream, _persist
from .owned_content_review import _reader, _json, _sha
from .owned_native_run import run_owned_native
from .online_probe_exposure import observe_probe_exposure
from .v2.native_development_evidence import collect_native_evidence


def prepare_online_probe(stream_directory, indices, candidates, *, directory):
    stream = OnlineStream(stream_directory)
    plan = stream.probe_snapshot(indices, candidates)
    root = Path(directory).absolute()
    if root.resolve() != root or root.exists() or root.is_relative_to(stream.root):
        raise ValueError("probe directory must be new, outside stream and free of symlinks")
    return plan


async def run_online_probe(stream_directory, indices, candidates, *, directory):
    plan = prepare_online_probe(stream_directory, indices, candidates, directory=directory)
    stream, root = OnlineStream(stream_directory), Path(directory).absolute()
    quota = stream.shared_budget()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    _persist(root / "manifest.json", plan)
    parent_paths = []
    for index, value in enumerate(plan["candidates"]):
        path = root / f"parent-{index}.json"
        _persist(path, value)
        parent_paths.append((path, _sha(path.read_bytes())))
    tasks = [BenchmarkTask.from_dict({**value, "split": "development"}) for value in plan["tasks"]]
    # Check every parent/task combination before starting any lease.
    for task in tasks:
        for path, digest in parent_paths:
            owned_candidate_identity(task, path, digest, plan["repository"], development_only=True)
    report = {"schema_version": "praxile.online_probe_report.v1", "track": plan["track"],
        "source_ledger_sha256": plan["source_ledger_sha256"], "status": "outcome_unknown", "attempts": [],
        "automatic_replay_allowed": False, "official_evaluation": False, "promotion_eligible": False,
        "candidate_adopted": False, "task_success_proven": False}
    _persist(root / "report.json", report)
    try:
        for task_index, task in zip(indices, tasks):
            for arm in ("baseline", "first", "second"):
                if stream.status()["ledger_sha256"] != plan["source_ledger_sha256"]:
                    raise ValueError("stream advanced during probe")
                attempt = {"task_index": task_index, "task_id": task.task_id, "arm": arm, "status": "outcome_unknown"}
                report["attempts"].append(attempt)
                _persist(root / "report.json", report)
                options = dict(plan["execution"])
                for key in ("wheel", "credential_file"):
                    options[key] = Path(options[key])
                extra = {"shared_chat_budget": quota} if quota else {}
                parent, identity = None, None
                if arm != "baseline":
                    path, digest = parent_paths[0 if arm == "first" else 1]
                    parent = plan["candidates"][0 if arm == "first" else 1]
                    identity = owned_candidate_identity(task, path, digest, plan["repository"], development_only=True)
                    extra.update(development_candidate_file=path, development_candidate_sha256=digest,
                                 development_only=True, repository=plan["repository"])
                source = plan["input_source_roots"][task.task_id]
                attempt_root = root / f"task-{task_index}" / arm
                path = await run_owned_native(task, state_root=attempt_root, image=task.environment.revision,
                    input_source_root=Path(source) if source else None, **options, **extra)
                path = Path(path)
                path.relative_to(attempt_root)
                read = _reader(path.parent)
                raw = read(path.name, max_bytes=16 * 1024**2)
                execution = _json(raw)
                trace_raw = read("agent/praxile-result.json", max_bytes=16 * 1024**2)
                if (execution.get("task_fingerprint") != task.fingerprint
                        or execution.get("status") != "executor_returned" or execution.get("cleanup_verified") is not True
                        or execution.get("source_result_sha256") != _sha(trace_raw)):
                    raise ValueError("probe lacks bound terminal execution evidence")
                trace = _json(trace_raw)
                exposure = observe_probe_exposure(trace, execution, candidate=parent, identity=identity,
                    task_local_enabled=options.get("task_local_memory_enabled", False))
                native_id = trace.get("trajectory", {}).get("task_id")
                evidence = collect_native_evidence(path.parent / "agent/praxile-result.json",
                    expected_sha256=_sha(trace_raw), development_task_ids=[native_id], heldout_task_ids=[],
                    track="development", limit=20)
                attempt.update(status="executor_returned", report=path.relative_to(root).as_posix(),
                    report_sha256=_sha(raw), trajectory_sha256=_sha(trace_raw),
                    evidence=evidence, required_outputs_collected=execution.get("required_outputs_collected"),
                    candidate_exposure_verified=exposure["candidate_exposure_verified"], mechanisms=exposure)
                _persist(root / "report.json", report)
        if stream.status()["ledger_sha256"] != plan["source_ledger_sha256"]:
            raise ValueError("stream advanced during probe")
        report["status"] = "executions_returned_not_scored"
    except BaseException as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        if quota:
            report["shared_chat_budget_end"] = quota.status()
        _persist(root / "report.json", report)
    return root / "report.json"
