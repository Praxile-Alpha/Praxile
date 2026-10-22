"""Sequential, approval-gated native stream; uncertain dispatches never replay."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import uuid

from ..utils import file_lock, write_json
from .contracts import BenchmarkTask
from .native_context import plan_candidate
from .owned_content_review import _reader, _json
from .owned_native_run import run_owned_native
from .v2.candidate import ContextCandidate
from .v2.schema import canonical_json


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _persist(path, state):
    payload = (json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(payload) > 16 * 1024**2:
        raise ValueError("stream ledger exceeds 16 MiB")
    temporary = path.with_name("." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _paths(value):
    if isinstance(value, Path):
        return str(value.absolute())
    if isinstance(value, dict):
        return {key: _paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_paths(item) for item in value]
    return value


class OnlineStream:
    """Local trusted-controller ledger, not a signed provenance service.

    Each task gets a new lease and frozen execution options. Only explicit human
    approval changes the candidate for subsequent tasks; no production updates.
    """

    @classmethod
    def create(cls, directory, tasks, *, execution, repository, input_source_roots=None):
        tasks = list(tasks)
        if (not tasks or len(tasks) > 10000 or any(not isinstance(task, BenchmarkTask)
                or task.split != "online_stream" or task.environment.kind != "harbor" for task in tasks)
                or len({task.task_id for task in tasks}) != len(tasks)):
            raise ValueError("stream requires unique ordered online_stream Harbor tasks")
        required = {"wheel", "wheel_sha256", "model", "base_url", "credential_file"}
        optional = {"max_steps", "timeout_seconds", "dependency_wheels", "required_capabilities",
                    "model_request_options", "task_local_memory_enabled"}
        if not isinstance(execution, dict) or not required <= execution.keys() or set(execution) - required - optional:
            raise ValueError("stream execution options must be explicit and cannot override protocol")
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("stream repository is required")
        roots = input_source_roots or {}
        if set(roots) - {task.task_id for task in tasks} or any(
                bool(task.inputs) != (roots.get(task.task_id) is not None) for task in tasks):
            raise ValueError("stream input roots differ from public task inputs")
        spec = _paths({"tasks": [task.to_dict() for task in tasks], "execution": execution,
                       "repository": repository, "input_source_roots": roots})
        if len(canonical_json(spec).encode()) > 8 * 1024**2:
            raise ValueError("stream plan exceeds 8 MiB")
        _digest(spec)
        root = Path(directory).absolute()
        if root.resolve() != root:
            raise ValueError("stream directory cannot contain symlinks")
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        _persist(root / "stream.json", {"schema_version": "praxile.online_stream.v1", "spec": spec,
                                         "spec_sha256": _digest(spec), "events": []})
        return cls(root)

    def __init__(self, directory):
        self.root = Path(directory).absolute()
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("unsafe stream directory")
        self._read()

    def _read(self):
        state = _json(_reader(self.root)("stream.json", max_bytes=16 * 1024**2))
        if (state.get("schema_version") != "praxile.online_stream.v1"
                or state.get("spec_sha256") != _digest(state.get("spec"))
                or not isinstance(state.get("events"), list)):
            raise ValueError("invalid stream ledger")
        previous = state["spec_sha256"]
        completed, pending, candidate = [], None, None
        tasks = state["spec"]["tasks"]
        for event in state["events"]:
            body = {key: value for key, value in event.items() if key != "sha256"}
            if event.get("previous_sha256") != previous or event.get("sha256") != _digest(body):
                raise ValueError("stream event chain mismatch")
            previous = event["sha256"]
            kind, data = event["kind"], event["data"]
            if kind == "dispatch":
                expected = candidate.digest if candidate else None
                if (pending is not None or len(completed) >= len(tasks)
                        or data != {"index": len(completed), "candidate_digest": expected}):
                    raise ValueError("stream dispatch order or candidate mismatch")
                pending = len(completed)
            elif kind == "completed":
                if pending is None or data["index"] != pending:
                    raise ValueError("stream completion has no matching dispatch")
                for path, digest in data["evidence"].items():
                    raw = _reader(self.root)(path, max_bytes=16 * 1024**2)
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise ValueError("completed stream evidence changed")
                completed.append(tasks[pending]["task_id"])
                pending = None
            elif kind == "approved_candidate":
                if pending is not None or data["after_tasks"] != completed or not data.get("approved_by"):
                    raise ValueError("candidate approval is not at a completed task boundary")
                candidate = self._candidate(data["candidate"], completed, state["spec"]["repository"])
            elif kind == "outcome_unknown":
                if pending is None or data["index"] != pending:
                    raise ValueError("unknown outcome has no dispatch")
            else:
                raise ValueError("unsupported stream event")
        return state, completed, pending, candidate

    @staticmethod
    def _candidate(value, completed, repository):
        candidate = ContextCandidate.from_dict(value)
        if not candidate.source_task_ids or not set(candidate.source_task_ids) <= set(completed):
            raise ValueError("candidate sources must belong to completed stream prefix")
        plan_candidate(value, "Admission only", repository, development_only=False, online_stream=True)
        return candidate

    def _append(self, state, kind, data):
        body = {"kind": kind, "data": data, "previous_sha256":
                state["events"][-1]["sha256"] if state["events"] else state["spec_sha256"]}
        state["events"].append({**body, "sha256": _digest(body)})
        if (self.root / "stream.json").is_symlink():
            raise ValueError("unsafe stream ledger")
        _persist(self.root / "stream.json", state)

    def status(self):
        state, completed, pending, candidate = self._read()
        return {"track": "online_stream", "completed_task_ids": completed, "pending_index": pending,
                "task_count": len(state["spec"]["tasks"]), "candidate_digest": candidate.digest if candidate else None,
                "ledger_sha256": state["events"][-1]["sha256"] if state["events"] else state["spec_sha256"],
                "promotion_eligible": False, "official_evaluation": False}

    def accept_candidate(self, value, *, approved=False, approved_by=""):
        if approved is not True or not isinstance(approved_by, str) or not approved_by.strip():
            raise ValueError("candidate update requires explicit human approval identity")
        with file_lock(self.root / "stream"):
            state, completed, pending, _ = self._read()
            if pending is not None or not completed or len(completed) == len(state["spec"]["tasks"]):
                raise ValueError("candidate updates require a completed prefix and a future task")
            candidate = self._candidate(deepcopy(value), completed, state["spec"]["repository"])
            self._append(state, "approved_candidate", {"candidate": candidate.to_dict(),
                         "after_tasks": completed, "approved_by": approved_by})
        return self.status()

    async def run_next(self):
        with file_lock(self.root / "stream"):
            state, completed, pending, candidate = self._read()
            if pending is not None or len(completed) >= len(state["spec"]["tasks"]):
                raise ValueError("stream finished or dispatch outcome unknown; automatic replay forbidden")
            index = len(completed)
            task = BenchmarkTask.from_dict(state["spec"]["tasks"][index])
            options = deepcopy(state["spec"]["execution"])
            for key in ("wheel", "credential_file"):
                options[key] = Path(options[key]) if options[key] is not None else None
            extra = {}
            if candidate:
                path = self.root / f"candidate-{len(state['events'])}.json"
                if path.exists() or path.is_symlink():
                    raise ValueError("candidate snapshot already exists")
                write_json(path, candidate.to_dict())
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                extra = {"development_candidate_file": path, "development_candidate_sha256": digest,
                         "repository": state["spec"]["repository"], "online_protocol": {
                             "completed_task_ids": completed, "task_id": task.task_id,
                             "candidate_file_sha256": digest, "repository": state["spec"]["repository"]}}
            self._append(state, "dispatch", {"index": index, "candidate_digest": candidate.digest if candidate else None})
        try:
            step_root = self.root / "tasks" / str(index)
            source_root = state["spec"]["input_source_roots"].get(task.task_id)
            result = await run_owned_native(task, state_root=step_root, image=task.environment.revision,
                input_source_root=Path(source_root) if source_root is not None else None, **options, **extra)
            relative = Path(result).relative_to(self.root).as_posix()
            if not relative.startswith(f"tasks/{index}/"):
                raise ValueError("execution report belongs to a different stream step")
            read = _reader(self.root)
            raw = read(relative, max_bytes=16 * 1024**2)
            report = _json(raw)
            if (report.get("task_fingerprint") != task.fingerprint or report.get("status") != "executor_returned"
                    or report.get("cleanup_verified") is not True):
                raise ValueError("stream execution lacks terminal cleanup evidence")
            trajectory = str(Path(relative).parent / "agent/praxile-result.json")
            trace_raw = read(trajectory, max_bytes=16 * 1024**2)
            trace_digest = hashlib.sha256(trace_raw).hexdigest()
            if trace_digest != report.get("source_result_sha256"):
                raise ValueError("stream trajectory digest mismatch")
            with file_lock(self.root / "stream"):
                current, _, pending, _ = self._read()
                if pending != index:
                    raise ValueError("stream dispatch changed during execution")
                self._append(current, "completed", {"index": index, "evidence": {
                    relative: hashlib.sha256(raw).hexdigest(), trajectory: trace_digest}})
        except BaseException as error:
            with file_lock(self.root / "stream"):
                current, _, _, _ = self._read()
                self._append(current, "outcome_unknown", {"index": index, "error_type": type(error).__name__})
            raise
        return self.status()
