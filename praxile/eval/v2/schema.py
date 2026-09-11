from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...adapters import AdapterTask


EVAL_TASK_SCHEMA_VERSION = "praxile.eval_task.v2"
EVAL_TASK_SET_SCHEMA_VERSION = "praxile.eval_task_set.v2"
EVAL_RESULT_SCHEMA_VERSION = "praxile.eval_result.v2"


class EvalSchemaError(ValueError):
    """Raised when a V2 benchmark record violates the frozen eval contract."""


@dataclass(frozen=True)
class RepositorySpec:
    repo: str
    base_commit: str
    clone_url: str
    environment_setup_commit: str | None = None

    def __post_init__(self) -> None:
        for name in ("repo", "base_commit", "clone_url"):
            _non_empty(getattr(self, name), name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepositorySpec":
        return cls(
            repo=str(value.get("repo") or ""),
            base_commit=str(value.get("base_commit") or ""),
            clone_url=str(value.get("clone_url") or ""),
            environment_setup_commit=_optional_string(value.get("environment_setup_commit")),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {"repo": self.repo, "base_commit": self.base_commit, "clone_url": self.clone_url}
        if self.environment_setup_commit:
            result["environment_setup_commit"] = self.environment_setup_commit
        return result


@dataclass(frozen=True)
class SWEbenchEvaluationSpec:
    dataset_name: str
    split: str
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    test_patch: str = ""
    reference_patch: str = ""
    image_assets: Mapping[str, Any] = field(default_factory=dict)
    private_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty(self.dataset_name, "dataset_name")
        _non_empty(self.split, "split")
        _string_tuple(self.fail_to_pass, "fail_to_pass")
        _string_tuple(self.pass_to_pass, "pass_to_pass")
        _json_object(self.image_assets, "image_assets")
        _json_object(self.private_metadata, "private_metadata")
        if not isinstance(self.test_patch, str) or not isinstance(self.reference_patch, str):
            raise EvalSchemaError("test_patch and reference_patch must be strings")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SWEbenchEvaluationSpec":
        image_assets = value.get("image_assets", {})
        private_metadata = value.get("private_metadata", {})
        if not isinstance(image_assets, Mapping) or not isinstance(private_metadata, Mapping):
            raise EvalSchemaError("image_assets and private_metadata must be objects")
        return cls(
            dataset_name=str(value.get("dataset_name") or ""),
            split=str(value.get("split") or ""),
            fail_to_pass=_strings(value.get("fail_to_pass"), "fail_to_pass"),
            pass_to_pass=_strings(value.get("pass_to_pass"), "pass_to_pass"),
            test_patch=str(value.get("test_patch") or ""),
            reference_patch=str(value.get("reference_patch") or ""),
            image_assets=dict(image_assets),
            private_metadata=dict(private_metadata),
        )

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "dataset_name": self.dataset_name,
            "split": self.split,
            "fail_to_pass": list(self.fail_to_pass),
            "pass_to_pass": list(self.pass_to_pass),
        }
        if include_private:
            result.update(
                {
                    "test_patch": self.test_patch,
                    "reference_patch": self.reference_patch,
                    "image_assets": dict(self.image_assets),
                    "private_metadata": dict(self.private_metadata),
                }
            )
        return result


@dataclass(frozen=True)
class EvalTask:
    task_id: str
    instruction: str
    repository: RepositorySpec
    evaluation: SWEbenchEvaluationSpec
    benchmark: str = "swe-bench"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = EVAL_TASK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("schema_version", "task_id", "instruction", "benchmark"):
            _non_empty(getattr(self, name), name)
        _json_object(self.metadata, "task metadata")
        if self.schema_version != EVAL_TASK_SCHEMA_VERSION:
            raise EvalSchemaError(f"unsupported eval task schema: {self.schema_version}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", self.task_id):
            raise EvalSchemaError(f"task_id is unsafe for local evidence storage: {self.task_id!r}")

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict(include_private=True))

    def to_adapter_task(self, project_root: str, *, trace_id: str, run_id: str) -> AdapterTask:
        # Evaluation-only fields deliberately never cross the adapter boundary.
        return AdapterTask(
            task_id=self.task_id,
            instruction=self.instruction,
            project_root=project_root,
            metadata={
                **dict(self.metadata),
                "trace_id": trace_id,
                "run_id": run_id,
                "benchmark": self.benchmark,
                "repository": self.repository.repo,
                "base_commit": self.repository.base_commit,
            },
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvalTask":
        repository = value.get("repository")
        evaluation = value.get("evaluation")
        if not isinstance(repository, Mapping) or not isinstance(evaluation, Mapping):
            raise EvalSchemaError("task repository and evaluation must be objects")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise EvalSchemaError("task metadata must be an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            task_id=str(value.get("task_id") or ""),
            instruction=str(value.get("instruction") or ""),
            benchmark=str(value.get("benchmark") or ""),
            repository=RepositorySpec.from_dict(repository),
            evaluation=SWEbenchEvaluationSpec.from_dict(evaluation),
            metadata=dict(metadata),
        )

    def to_dict(self, *, include_private: bool = True) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "instruction": self.instruction,
            "benchmark": self.benchmark,
            "repository": self.repository.to_dict(),
            "evaluation": self.evaluation.to_dict(include_private=include_private),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvalTaskSet:
    name: str
    dataset_name: str
    split: str
    tasks: tuple[EvalTask, ...]
    selection: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = EVAL_TASK_SET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("schema_version", "name", "dataset_name", "split"):
            _non_empty(getattr(self, name), name)
        if not self.tasks:
            raise EvalSchemaError("task set must contain at least one task")
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise EvalSchemaError("task set contains duplicate task_id values")
        _json_object(self.selection, "selection")
        if self.schema_version != EVAL_TASK_SET_SCHEMA_VERSION:
            raise EvalSchemaError(f"unsupported eval task-set schema: {self.schema_version}")

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvalTaskSet":
        raw_tasks = value.get("tasks")
        if not isinstance(raw_tasks, list):
            raise EvalSchemaError("task set tasks must be an array")
        if any(not isinstance(item, Mapping) for item in raw_tasks):
            raise EvalSchemaError("every task set item must be an object")
        selection = value.get("selection", {})
        if not isinstance(selection, Mapping):
            raise EvalSchemaError("task set selection must be an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            name=str(value.get("name") or ""),
            dataset_name=str(value.get("dataset_name") or ""),
            split=str(value.get("split") or ""),
            tasks=tuple(EvalTask.from_dict(item) for item in raw_tasks),
            selection=dict(selection),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "tasks": [task.to_dict(include_private=True) for task in self.tasks],
            "selection": dict(self.selection),
        }


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EvalSchemaError(f"value must be canonical JSON: {exc}") from exc


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvalSchemaError(f"{name} must be a non-empty string")


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None and str(value).strip() else None


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise EvalSchemaError(f"{name} must be an array of strings")
    result = tuple(str(item) for item in value)
    _string_tuple(result, name)
    return result


def _string_tuple(value: tuple[str, ...], name: str) -> None:
    if any(not isinstance(item, str) or not item for item in value):
        raise EvalSchemaError(f"{name} must contain non-empty strings")


def _json_object(value: Mapping[str, Any], name: str) -> None:
    if not isinstance(value, Mapping):
        raise EvalSchemaError(f"{name} must be an object")
    canonical_json(dict(value))
