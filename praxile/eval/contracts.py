"""Benchmark-neutral records; private evaluator references never enter agent views."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from ..adapters import AdapterTask
from .v2.schema import EvalSchemaError, EvalTask, canonical_json


TASK_VERSION = "praxile.benchmark_task.v1"
RESULT_VERSION = "praxile.benchmark_outcome.v1"


def _text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvalSchemaError(f"{name} must be a non-empty string")


def _relative(value: str) -> None:
    _text(value, "artifact path")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
            or "\x00" in value or path.as_posix() != value or value == "."):
        raise EvalSchemaError(f"artifact path must be a normalized relative path: {value!r}")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise EvalSchemaError("content digest must be sha256:<64 lowercase hex characters>")


def _record(cls: type, value: Any):
    if not isinstance(value, Mapping):
        raise EvalSchemaError(f"{cls.__name__} must be an object")
    try:
        return cls(**dict(value))
    except TypeError as exc:
        raise EvalSchemaError(f"invalid {cls.__name__}: {exc}") from exc


@dataclass(frozen=True)
class InputArtifact:
    path: str
    source_ref: str
    content_digest: str

    def __post_init__(self) -> None:
        _relative(self.path)
        _text(self.source_ref, "source_ref")
        _hash(self.content_digest)


@dataclass(frozen=True)
class OutputContract:
    path: str
    media_type: str
    required: bool = True

    def __post_init__(self) -> None:
        _relative(self.path)
        _text(self.media_type, "media_type")
        if type(self.required) is not bool:
            raise EvalSchemaError("output required must be boolean")


@dataclass(frozen=True)
class EnvironmentSpec:
    kind: str
    source_ref: str
    revision: str

    def __post_init__(self) -> None:
        if self.kind not in {"git_worktree", "file_workspace", "harbor"}:
            raise EvalSchemaError(f"unsupported environment kind: {self.kind}")
        _text(self.source_ref, "environment source_ref")
        _text(self.revision, "environment revision")


@dataclass(frozen=True)
class EvaluatorReference:
    name: str
    version: str
    private_ref: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in ("name", "version", "private_ref"):
            _text(getattr(self, name), name)
        _hash(self.content_digest)


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    benchmark: str
    dataset_revision: str
    split: str
    instruction: str
    environment: EnvironmentSpec
    evaluator: EvaluatorReference
    inputs: tuple[InputArtifact, ...] = ()
    outputs: tuple[OutputContract, ...] = ()
    schema_version: str = TASK_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TASK_VERSION:
            raise EvalSchemaError(f"unsupported task schema: {self.schema_version}")
        for name in ("task_id", "benchmark", "dataset_revision", "split", "instruction"):
            _text(getattr(self, name), name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", self.task_id):
            raise EvalSchemaError("unsafe task_id")
        if not isinstance(self.environment, EnvironmentSpec) or not isinstance(self.evaluator, EvaluatorReference):
            raise EvalSchemaError("task environment and evaluator must be typed records")
        for name, cls in (("inputs", InputArtifact), ("outputs", OutputContract)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, cls) for item in values):
                raise EvalSchemaError(f"{name} must be a tuple of {cls.__name__}")
            paths = [item.path for item in values]
            if len(set(paths)) != len(paths):
                raise EvalSchemaError(f"duplicate {name} paths")
        inputs = {item.path for item in self.inputs}
        if inputs.intersection(item.path for item in self.outputs):
            raise EvalSchemaError("read-only input and output paths overlap")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["inputs"] = [asdict(item) for item in self.inputs]
        value["outputs"] = [asdict(item) for item in self.outputs]
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkTask":
        if not isinstance(value, Mapping):
            raise EvalSchemaError("task must be an object")
        record = dict(value)
        for name, kind in (("inputs", InputArtifact), ("outputs", OutputContract)):
            values = record.get(name, [])
            if not isinstance(values, list):
                raise EvalSchemaError(f"{name} must be an array")
            record[name] = tuple(_record(kind, item) for item in values)
        record["environment"] = _record(EnvironmentSpec, record.get("environment"))
        record["evaluator"] = _record(EvaluatorReference, record.get("evaluator"))
        return _record(cls, record)

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict())

    def to_adapter_task(self, project_root: str, *, trace_id: str, run_id: str) -> AdapterTask:
        # Allowlist projection: neither source locations nor evaluator references are public.
        return AdapterTask(
            task_id=self.task_id, instruction=self.instruction, project_root=project_root,
            metadata={
                "trace_id": trace_id, "run_id": run_id, "benchmark": self.benchmark,
                "inputs": [{"path": item.path, "content_digest": item.content_digest} for item in self.inputs],
                "outputs": [asdict(item) for item in self.outputs],
            },
        )

    @classmethod
    def from_swebench(
        cls, task: EvalTask, *, dataset_revision: str, private_ref: str,
    ) -> "BenchmarkTask":
        """Project V2 without mutating its serialization, fingerprint, or resume state."""
        return cls(
            task_id=task.task_id, benchmark=task.benchmark, dataset_revision=dataset_revision,
            split=task.evaluation.split, instruction=task.instruction,
            environment=EnvironmentSpec("git_worktree", task.repository.clone_url, task.repository.base_commit),
            evaluator=EvaluatorReference(
                "swe-bench-official", "1", private_ref,
                _digest(task.evaluation.to_dict(include_private=True)),
            ),
        )


@dataclass(frozen=True)
class BenchmarkScore:
    name: str
    value: float
    maximum: float

    def __post_init__(self) -> None:
        _text(self.name, "score name")
        for value in (self.value, self.maximum):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise EvalSchemaError("scores must be finite numbers")
        if self.maximum <= 0 or not 0 <= self.value <= self.maximum:
            raise EvalSchemaError("score must lie between zero and its positive maximum")


@dataclass(frozen=True)
class BenchmarkOutcome:
    task_id: str
    status: str
    scores: tuple[BenchmarkScore, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = RESULT_VERSION

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        if self.schema_version != RESULT_VERSION:
            raise EvalSchemaError("unsupported outcome schema")
        if self.status not in {"completed", "failed", "timed_out", "infra_error", "judge_unavailable", "cancelled", "unsupported"}:
            raise EvalSchemaError("unsupported outcome status")
        if not isinstance(self.scores, tuple) or any(not isinstance(item, BenchmarkScore) for item in self.scores):
            raise EvalSchemaError("scores must be a tuple of BenchmarkScore")
        if len({item.name for item in self.scores}) != len(self.scores):
            raise EvalSchemaError("duplicate score names")
        if self.status == "completed" and not self.scores:
            raise EvalSchemaError("completed evaluation requires scores")
        if self.status != "completed" and self.scores:
            raise EvalSchemaError("incomplete evaluation cannot publish scores")
        if not isinstance(self.evidence_refs, tuple):
            raise EvalSchemaError("evidence_refs must be a tuple")
        for ref in self.evidence_refs:
            _text(ref, "evidence_ref")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "task_id": self.task_id,
                "status": self.status, "scores": [asdict(item) for item in self.scores],
                "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BenchmarkOutcome":
        if not isinstance(value, Mapping):
            raise EvalSchemaError("outcome must be an object")
        record = dict(value)
        if not isinstance(record.get("scores", []), list) or not isinstance(record.get("evidence_refs", []), list):
            raise EvalSchemaError("scores and evidence_refs must be arrays")
        record["scores"] = tuple(_record(BenchmarkScore, item) for item in record.get("scores", []))
        record["evidence_refs"] = tuple(record.get("evidence_refs", []))
        return _record(cls, record)
