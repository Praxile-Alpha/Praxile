from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ...adapters import AdapterPolicy
from .schema import EvalSchemaError, canonical_json


CONTEXT_CANDIDATE_SCHEMA_VERSION = "praxile.context_candidate.v1"


@dataclass(frozen=True)
class ContextCandidate:
    candidate_id: str
    version: str
    title: str
    candidate_type: str
    context_item: Mapping[str, Any]
    confidence: float
    evidence_refs: tuple[str, ...]
    expected_effect: Mapping[str, Any]
    source_diagnosis_ids: tuple[str, ...] = ()
    source_task_ids: tuple[str, ...] = ()
    applies_to: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTEXT_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_CANDIDATE_SCHEMA_VERSION:
            raise EvalSchemaError(f"unsupported context candidate schema: {self.schema_version}")
        for name in ("candidate_id", "version", "title", "candidate_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise EvalSchemaError(f"{name} must be a non-empty string")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.candidate_id):
            raise EvalSchemaError(f"unsafe candidate_id: {self.candidate_id!r}")
        if self.candidate_type not in {"experience_activation", "context_policy"}:
            raise EvalSchemaError(f"unsupported candidate_type: {self.candidate_type}")
        if not isinstance(self.context_item, Mapping) or not self.context_item:
            raise EvalSchemaError("context candidate must contain exactly one non-empty context_item")
        content = self.context_item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise EvalSchemaError("context_item.content must be a non-empty string")
        if not 0.0 <= self.confidence <= 1.0:
            raise EvalSchemaError("candidate confidence must be between 0 and 1")
        if not self.evidence_refs or any(
            not isinstance(item, str) or not item.startswith(("event:", "artifact:"))
            for item in self.evidence_refs
        ):
            raise EvalSchemaError("candidate evidence_refs must contain event/artifact references")
        if not isinstance(self.expected_effect, Mapping) or not self.expected_effect:
            raise EvalSchemaError("expected_effect must be a non-empty object")
        for name, values in (
            ("source_diagnosis_ids", self.source_diagnosis_ids),
            ("source_task_ids", self.source_task_ids),
        ):
            if any(not isinstance(item, str) or not item for item in values):
                raise EvalSchemaError(f"{name} must contain non-empty strings")
        if not isinstance(self.applies_to, Mapping):
            raise EvalSchemaError("applies_to must be an object")
        canonical_json(self.to_dict())

    @property
    def digest(self) -> str:
        payload = canonical_json(self.to_dict()).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "ContextCandidate":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EvalSchemaError(f"{path}: invalid candidate JSON: {exc.msg}") from exc
        if not isinstance(value, Mapping):
            raise EvalSchemaError("context candidate must be a JSON object")
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextCandidate":
        context = value.get("context_item")
        applies_to = value.get("applies_to", {})
        expected_effect = value.get("expected_effect")
        if not all(isinstance(item, Mapping) for item in (context, applies_to, expected_effect)):
            raise EvalSchemaError("context_item, applies_to, and expected_effect must be objects")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            candidate_id=str(value.get("candidate_id") or ""),
            version=str(value.get("version") or ""),
            title=str(value.get("title") or ""),
            candidate_type=str(value.get("candidate_type") or ""),
            context_item=dict(context),
            confidence=float(value.get("confidence", -1.0)),
            evidence_refs=_strings(value.get("evidence_refs", []), "evidence_refs"),
            expected_effect=dict(expected_effect),
            source_diagnosis_ids=_strings(value.get("source_diagnosis_ids", []), "source_diagnosis_ids"),
            source_task_ids=_strings(value.get("source_task_ids", []), "source_task_ids"),
            applies_to=dict(applies_to),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "version": self.version,
            "title": self.title,
            "candidate_type": self.candidate_type,
            "context_item": dict(self.context_item),
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "expected_effect": dict(self.expected_effect),
            "source_diagnosis_ids": list(self.source_diagnosis_ids),
            "source_task_ids": list(self.source_task_ids),
            "applies_to": dict(self.applies_to),
        }

    def validate_clean_track(self, evaluation_task_ids: set[str]) -> None:
        overlap = sorted(evaluation_task_ids & set(self.source_task_ids))
        if overlap:
            raise EvalSchemaError(
                f"clean-track candidate source tasks overlap evaluation tasks: {overlap}"
            )

    def policy(self, baseline: AdapterPolicy) -> AdapterPolicy:
        if baseline.context:
            raise EvalSchemaError("P0 baseline policy must not contain context")
        item = {
            **dict(self.context_item),
            "candidate_id": self.candidate_id,
            "candidate_version": self.version,
            "candidate_digest": self.digest,
        }
        return AdapterPolicy(
            policy_id=self.candidate_id,
            version=self.version,
            context=(item,),
            budgets=dict(baseline.budgets),
            settings=dict(baseline.settings),
        )


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item for item in value):
        raise EvalSchemaError(f"{name} must be an array of non-empty strings")
    return tuple(value)
