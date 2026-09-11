from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...adapters import AdapterPolicy, AgentAdapterV2, validate_adapter_v2
from ...utils import file_lock, utc_now
from .schema import EvalSchemaError, EvalTaskSet, canonical_json


EVAL_MANIFEST_SCHEMA_VERSION = "praxile.eval_manifest.v2"


@dataclass(frozen=True)
class EvalRunManifest:
    eval_run_id: str
    created_at: str
    reproducibility: Mapping[str, Any]
    reproducibility_digest: str
    experiment_variable: Mapping[str, Any]
    schema_version: str = EVAL_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EVAL_MANIFEST_SCHEMA_VERSION:
            raise EvalSchemaError(f"unsupported eval manifest schema: {self.schema_version}")
        expected = _digest(self.reproducibility)
        if self.reproducibility_digest != expected:
            raise EvalSchemaError("eval manifest reproducibility digest does not match its payload")
        canonical_json(dict(self.experiment_variable))

    @classmethod
    def create(
        cls,
        *,
        eval_run_id: str,
        task_set: EvalTaskSet,
        adapter: AgentAdapterV2,
        policy: AdapterPolicy,
        model: Mapping[str, Any],
        evaluator: Mapping[str, Any],
        execution: Mapping[str, Any],
        experiment_variable: Mapping[str, Any] | None = None,
    ) -> "EvalRunManifest":
        capabilities = validate_adapter_v2(adapter)
        try:
            praxile_version = importlib.metadata.version("praxile")
        except importlib.metadata.PackageNotFoundError:
            praxile_version = "source-tree"
        reproducibility = {
            "task_set": {
                "name": task_set.name,
                "dataset_name": task_set.dataset_name,
                "split": task_set.split,
                "digest": task_set.digest,
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "fingerprint": task.fingerprint,
                        "repo": task.repository.repo,
                        "base_commit": task.repository.base_commit,
                    }
                    for task in task_set.tasks
                ],
                "selection": dict(task_set.selection),
            },
            "adapter": {
                "name": adapter.name,
                "protocol_version": adapter.protocol_version,
                "capabilities": capabilities.to_dict(),
            },
            "model": _redact(dict(model)),
            "policy": _redact(policy.to_dict()),
            "evaluator": _redact(dict(evaluator)),
            "execution": _redact(dict(execution)),
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "praxile_version": praxile_version,
                "python_runtime": sys.version.split()[0],
            },
        }
        canonical_json(reproducibility)
        return cls(
            eval_run_id=eval_run_id,
            created_at=utc_now(),
            reproducibility=reproducibility,
            reproducibility_digest=_digest(reproducibility),
            experiment_variable=dict(experiment_variable or {}),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvalRunManifest":
        reproducibility = value.get("reproducibility")
        variable = value.get("experiment_variable", {})
        if not isinstance(reproducibility, Mapping) or not isinstance(variable, Mapping):
            raise EvalSchemaError("manifest reproducibility and experiment_variable must be objects")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            eval_run_id=str(value.get("eval_run_id") or ""),
            created_at=str(value.get("created_at") or ""),
            reproducibility=dict(reproducibility),
            reproducibility_digest=str(value.get("reproducibility_digest") or ""),
            experiment_variable=dict(variable),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "eval_run_id": self.eval_run_id,
            "created_at": self.created_at,
            "reproducibility": dict(self.reproducibility),
            "reproducibility_digest": self.reproducibility_digest,
            "experiment_variable": dict(self.experiment_variable),
        }


class ImmutableManifestStore:
    def __init__(self, state_root: Path):
        self.root = state_root.resolve() / "eval" / "v2" / "runs"

    def path_for(self, eval_run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", eval_run_id):
            raise EvalSchemaError(f"unsafe eval_run_id: {eval_run_id!r}")
        return self.root / eval_run_id / "manifest.json"

    def write_once(self, manifest: EvalRunManifest) -> Path:
        path = self.path_for(manifest.eval_run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        with file_lock(path):
            if path.exists():
                current = path.read_text(encoding="utf-8")
                if current != payload:
                    raise EvalSchemaError(f"immutable eval manifest already exists with different content: {path}")
                return path
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        return path

    def load(self, eval_run_id: str) -> EvalRunManifest:
        path = self.path_for(eval_run_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise EvalSchemaError("eval manifest must be an object")
        return EvalRunManifest.from_dict(value)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in ("api_key", "apikey", "token", "secret", "password", "authorization")):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
