from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import signal
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import unquote, urlparse

from ...adapters import AdapterRunResult
from ...utils import path_is_relative_to, utc_now
from .schema import EvalSchemaError, EvalTask


@dataclass(frozen=True)
class SWEbenchPrediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str

    def to_dict(self) -> dict[str, str]:
        # These names are the official SWE-bench harness handoff contract.
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model_name_or_path,
            "model_patch": self.model_patch,
        }

    def write_jsonl(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
        return path


@dataclass(frozen=True)
class EvaluatorResult:
    status: str
    resolved: bool | None
    started_at: str
    ended_at: str
    evidence_paths: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "evidence_paths": list(self.evidence_paths),
            "details": dict(self.details),
        }


class TaskEvaluator(Protocol):
    name: str
    version: str

    def identity(self) -> Mapping[str, Any]: ...

    def evaluate(self, task: EvalTask, prediction: SWEbenchPrediction, output_root: Path) -> EvaluatorResult: ...


class OfficialSWEbenchEvaluator:
    """Invoke the upstream Docker harness one instance at a time for failure isolation."""

    name = "swe-bench-official"
    version = "1"

    def __init__(
        self,
        *,
        python_executable: str = sys.executable,
        max_workers: int = 1,
        timeout_seconds: int = 1800,
        process_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        extra_args: Sequence[str] = (),
    ):
        self.python_executable = python_executable
        self.max_workers = max(1, int(max_workers))
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.process_runner = process_runner or _run_isolated_process
        self.extra_args = tuple(extra_args)

    def identity(self) -> Mapping[str, Any]:
        try:
            distribution_version = importlib.metadata.version("swebench")
        except importlib.metadata.PackageNotFoundError:
            distribution_version = None
        return {
            "name": self.name,
            "version": self.version,
            "entrypoint": "swebench.harness.run_evaluation",
            "distribution_version": distribution_version,
            "max_workers": self.max_workers,
            "timeout_seconds": self.timeout_seconds,
            "extra_args": list(self.extra_args),
        }

    def availability(self) -> tuple[bool, str]:
        try:
            distribution_version = importlib.metadata.version("swebench")
        except importlib.metadata.PackageNotFoundError:
            return False, "install the benchmark extra: python -m pip install 'praxile[benchmark]'"
        docker = shutil.which("docker")
        if not docker:
            return False, "the official local evaluator requires the Docker CLI"
        return True, f"swebench={distribution_version} docker={docker}"

    def evaluate(self, task: EvalTask, prediction: SWEbenchPrediction, output_root: Path) -> EvaluatorResult:
        output_root.mkdir(parents=True, exist_ok=True)
        prediction_path = prediction.write_jsonl(output_root / "prediction.jsonl")
        upstream_run_id = _safe_run_id(output_root.parent.name + "-" + task.task_id)
        command = [
            self.python_executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            task.evaluation.dataset_name,
            "--split",
            task.evaluation.split,
            "--predictions_path",
            str(prediction_path),
            "--instance_ids",
            task.task_id,
            "--max_workers",
            str(self.max_workers),
            "--run_id",
            upstream_run_id,
            "--timeout",
            str(self.timeout_seconds),
            *self.extra_args,
        ]
        started_at = utc_now()
        try:
            result = self.process_runner(command, cwd=output_root, timeout=self.timeout_seconds + 120)
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            result = None
            timed_out = True
            stdout = _text(exc.stdout)
            stderr = _text(exc.stderr)
        else:
            stdout, stderr = result.stdout, result.stderr
        (output_root / "evaluator.stdout.log").write_text(stdout or "", encoding="utf-8")
        (output_root / "evaluator.stderr.log").write_text(stderr or "", encoding="utf-8")
        evidence = [str(prediction_path), str(output_root / "evaluator.stdout.log"), str(output_root / "evaluator.stderr.log")]
        if timed_out:
            return EvaluatorResult(
                status="timed_out",
                resolved=None,
                started_at=started_at,
                ended_at=utc_now(),
                evidence_paths=tuple(evidence),
                details={"command": command, "timeout_seconds": self.timeout_seconds},
            )
        resolved, report_path = self._find_resolution(output_root, task.task_id)
        if report_path:
            evidence.append(str(report_path))
        return EvaluatorResult(
            status="completed" if result and result.returncode == 0 else "failed",
            resolved=resolved,
            started_at=started_at,
            ended_at=utc_now(),
            evidence_paths=tuple(evidence),
            details={"command": command, "returncode": result.returncode if result else None},
        )

    @staticmethod
    def _find_resolution(root: Path, task_id: str) -> tuple[bool | None, Path | None]:
        for path in sorted(root.rglob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            resolved = _resolution_from_report(value, task_id)
            if resolved is not None:
                return resolved, path
        return None, None


def prediction_from_adapter_result(
    task: EvalTask,
    result: AdapterRunResult,
    *,
    workspace_root: Path,
    model_name_or_path: str,
) -> SWEbenchPrediction:
    artifacts = [item for item in result.artifacts if item.type in {"workspace_patch", "patch", "diff"}]
    if not artifacts:
        raise EvalSchemaError(f"adapter run {result.handle.run_id} did not produce a patch artifact")
    artifact = artifacts[0]
    path = _artifact_path(artifact.uri, workspace_root)
    if not path.is_file() or not path_is_relative_to(path, workspace_root):
        raise EvalSchemaError(f"patch artifact escapes or is missing from the isolated workspace: {artifact.uri}")
    data = path.read_bytes()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if artifact.content_digest.startswith("sha256:") and artifact.content_digest != digest:
        raise EvalSchemaError(f"patch artifact digest mismatch: {artifact.artifact_id}")
    return SWEbenchPrediction(
        instance_id=task.task_id,
        model_name_or_path=model_name_or_path,
        model_patch=data.decode("utf-8"),
    )


def _artifact_path(uri: str, workspace_root: Path) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve()
    if parsed.scheme:
        raise EvalSchemaError(f"patch artifact must be file-backed, got {parsed.scheme!r}")
    return (workspace_root / uri).resolve()


def _resolution_from_report(value: Any, task_id: str) -> bool | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("resolved_ids", "instances_resolved", "resolved"):
        item = value.get(key)
        if isinstance(item, list):
            return task_id in {str(entry) for entry in item}
        if isinstance(item, Mapping) and task_id in item:
            return bool(item[task_id])
    instance = value.get(task_id)
    if isinstance(instance, Mapping):
        for key in ("resolved", "success"):
            if isinstance(instance.get(key), bool):
                return bool(instance[key])
    if str(value.get("instance_id") or "") == task_id:
        for key in ("resolved", "success"):
            if isinstance(value.get(key), bool):
                return bool(value[key])
    return None


def _safe_run_id(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "-" for character in value)[:120]


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _run_isolated_process(args: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":  # pragma: no cover - Windows CI coverage is separate
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        finally:
            stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr) from exc
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
