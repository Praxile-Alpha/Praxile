from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping


CONTROL_PLANE_SCHEMA_VERSION = "praxile.control_plane.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
EVIDENCE_TYPES = frozenset({"event", "artifact", "eval", "diagnosis", "run", "user_feedback"})
LIFECYCLE_STATUSES = frozenset({"proposed", "approved", "active", "deprecated", "rolled_back"})


class ControlPlaneSchemaError(ValueError):
    """Raised when a V2 control-plane record violates its versioned contract."""


@dataclass(frozen=True)
class EvidenceRef:
    type: str
    ref_id: str
    claim: str = ""

    def __post_init__(self) -> None:
        if self.type not in EVIDENCE_TYPES:
            raise ControlPlaneSchemaError(f"unsupported evidence type: {self.type!r}")
        safe_id(self.ref_id, "evidence ref_id")
        if not isinstance(self.claim, str):
            raise ControlPlaneSchemaError("evidence claim must be a string")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceRef":
        require_mapping(value, "evidence")
        return cls(type=str(value.get("type") or ""), ref_id=str(value.get("ref_id") or ""), claim=str(value.get("claim") or ""))

    def to_dict(self) -> dict[str, str]:
        result = {"type": self.type, "ref_id": self.ref_id}
        if self.claim:
            result["claim"] = self.claim
        return result

    @property
    def canonical_ref(self) -> str:
        return f"{self.type}:{self.ref_id}"


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ControlPlaneSchemaError(f"value must be canonical JSON: {exc}") from exc


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def non_empty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneSchemaError(f"{name} must be a non-empty string")
    return value


def safe_id(value: Any, name: str) -> str:
    non_empty(value, name)
    if not _SAFE_ID.fullmatch(value):
        raise ControlPlaneSchemaError(f"{name} is unsafe: {value!r}")
    return value


def require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ControlPlaneSchemaError(f"{name} must be an object")
    canonical_json(dict(value))
    return value


def strings(value: Any, name: str, *, required: bool = False) -> tuple[str, ...]:
    if value is None:
        value = ()
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ControlPlaneSchemaError(f"{name} must be an array of non-empty strings")
    result = tuple(value)
    if required and not result:
        raise ControlPlaneSchemaError(f"{name} must not be empty")
    return result


def evidence_refs(value: Any, *, required: bool = True) -> tuple[EvidenceRef, ...]:
    if not isinstance(value, (list, tuple)):
        raise ControlPlaneSchemaError("source_evidence must be an array")
    result = tuple(item if isinstance(item, EvidenceRef) else EvidenceRef.from_dict(item) for item in value)
    if required and not result:
        raise ControlPlaneSchemaError("source_evidence must not be empty")
    return result


def confidence(value: Any) -> float:
    if isinstance(value, bool):
        raise ControlPlaneSchemaError("confidence must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ControlPlaneSchemaError("confidence must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ControlPlaneSchemaError("confidence must be between 0 and 1")
    return result


def strict_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ControlPlaneSchemaError(f"{name} must be a boolean")
    return value


def strict_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlPlaneSchemaError(f"{name} must be an integer")
    if value < minimum:
        qualifier = "positive" if minimum == 1 else f"at least {minimum}"
        raise ControlPlaneSchemaError(f"{name} must be {qualifier}")
    return value
