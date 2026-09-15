from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from ..utils import new_id, utc_now


AGENT_EVENT_SCHEMA_VERSION = "praxile.agent_event.v1"
ARTIFACT_SCHEMA_VERSION = "praxile.artifact.v1"

KNOWN_EVENT_TYPES = frozenset(
    {
        "RUN_START",
        "MODEL_CALL",
        "CONTEXT_ACTIVATION",
        "CONTEXT_SOURCE_USAGE",
        "CONTEXT_INJECT",
        "TOOL_CALL",
        "TOOL_RESULT",
        "ARTIFACT_CHANGE",
        "VERIFICATION",
        "CHECKPOINT",
        "SUBAGENT_START",
        "SUBAGENT_END",
        "SKILL_REFERENCE",
        "FINAL_RESULT",
        "RUN_END",
    }
)

_EVENT_FIELDS = {
    "schema_version",
    "event_id",
    "timestamp",
    "trace_id",
    "run_id",
    "parent_run_id",
    "task_id",
    "type",
    "actor",
    "payload",
    "evidence_refs",
    "step_id",
    "tool_call_id",
    "artifact_ids",
    "token_usage",
    "latency_ms",
    "cost",
    "backend_sequence",
    "native_payload_ref",
}


class TraceSchemaError(ValueError):
    """Raised when a normalized trace record violates the frozen envelope."""


@dataclass(frozen=True)
class TraceIdentity:
    task_id: str
    trace_id: str
    run_id: str
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "trace_id", "run_id"):
            _non_empty(getattr(self, name), name)
        if self.parent_run_id is not None:
            _non_empty(self.parent_run_id, "parent_run_id")
        if self.parent_run_id == self.run_id:
            raise TraceSchemaError("parent_run_id must differ from run_id")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TraceIdentity":
        return cls(
            task_id=str(value.get("task_id") or ""),
            trace_id=str(value.get("trace_id") or ""),
            run_id=str(value.get("run_id") or ""),
            parent_run_id=_optional_string(value.get("parent_run_id")),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {"task_id": self.task_id, "trace_id": self.trace_id, "run_id": self.run_id}
        if self.parent_run_id is not None:
            result["parent_run_id"] = self.parent_run_id
        return result


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    cache: int = 0
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("input", "output", "cache"):
            if getattr(self, name) < 0:
                raise TraceSchemaError(f"token_usage.{name} must be non-negative")
        _json_value(dict(self.extensions), "token_usage.extensions")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TokenUsage":
        known = {"input", "output", "cache", "prompt_tokens", "completion_tokens", "cache_tokens"}
        return cls(
            input=_integer(value.get("input", value.get("prompt_tokens", 0)), "token_usage.input"),
            output=_integer(value.get("output", value.get("completion_tokens", 0)), "token_usage.output"),
            cache=_integer(value.get("cache", value.get("cache_tokens", 0)), "token_usage.cache"),
            extensions={key: item for key, item in value.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        return {**dict(self.extensions), "input": self.input, "output": self.output, "cache": self.cache}


@dataclass(frozen=True)
class AgentEvent:
    event_id: str
    timestamp: str
    trace_id: str
    run_id: str
    task_id: str
    type: str
    actor: str
    payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = AGENT_EVENT_SCHEMA_VERSION
    parent_run_id: str | None = None
    step_id: str | None = None
    tool_call_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    token_usage: TokenUsage | None = None
    latency_ms: int | None = None
    cost: float | None = None
    backend_sequence: int | None = None
    native_payload_ref: str | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("schema_version", "event_id", "timestamp", "trace_id", "run_id", "task_id", "type", "actor"):
            _non_empty(getattr(self, name), name)
        _timestamp(self.timestamp)
        if self.parent_run_id is not None:
            _non_empty(self.parent_run_id, "parent_run_id")
        if self.parent_run_id == self.run_id:
            raise TraceSchemaError("parent_run_id must differ from run_id")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise TraceSchemaError("latency_ms must be non-negative")
        if self.cost is not None and self.cost < 0:
            raise TraceSchemaError("cost must be non-negative")
        if self.backend_sequence is not None and self.backend_sequence < 0:
            raise TraceSchemaError("backend_sequence must be non-negative")
        _string_tuple(self.evidence_refs, "evidence_refs")
        _string_tuple(self.artifact_ids, "artifact_ids")
        _json_value(dict(self.payload), "payload")
        _json_value(dict(self.extensions), "extensions")
        collisions = _EVENT_FIELDS & set(self.extensions)
        if collisions:
            raise TraceSchemaError(f"extensions cannot replace envelope fields: {sorted(collisions)}")

    @property
    def is_known_type(self) -> bool:
        return self.type in KNOWN_EVENT_TYPES

    @property
    def identity(self) -> TraceIdentity:
        return TraceIdentity(
            task_id=self.task_id,
            trace_id=self.trace_id,
            run_id=self.run_id,
            parent_run_id=self.parent_run_id,
        )

    @classmethod
    def create(
        cls,
        *,
        trace_id: str,
        run_id: str,
        task_id: str,
        type: str,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> "AgentEvent":
        return cls(
            event_id=str(kwargs.pop("event_id", new_id("event"))),
            timestamp=str(kwargs.pop("timestamp", utc_now())),
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            type=type,
            actor=actor,
            payload=dict(payload or {}),
            **kwargs,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentEvent":
        if not isinstance(value, Mapping):
            raise TraceSchemaError("event must be a JSON object")
        token = value.get("token_usage")
        if token is not None and not isinstance(token, Mapping):
            raise TraceSchemaError("token_usage must be an object")
        payload = value.get("payload")
        if not isinstance(payload, Mapping):
            raise TraceSchemaError("payload must be an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            event_id=str(value.get("event_id") or ""),
            timestamp=str(value.get("timestamp") or ""),
            trace_id=str(value.get("trace_id") or ""),
            run_id=str(value.get("run_id") or ""),
            parent_run_id=_optional_string(value.get("parent_run_id")),
            task_id=str(value.get("task_id") or ""),
            type=str(value.get("type") or ""),
            actor=str(value.get("actor") or ""),
            payload=dict(payload),
            evidence_refs=_strings(value.get("evidence_refs"), "evidence_refs"),
            step_id=_optional_string(value.get("step_id")),
            tool_call_id=_optional_string(value.get("tool_call_id")),
            artifact_ids=_strings(value.get("artifact_ids"), "artifact_ids"),
            token_usage=TokenUsage.from_dict(token) if token is not None else None,
            latency_ms=_optional_integer(value.get("latency_ms"), "latency_ms"),
            cost=_optional_float(value.get("cost"), "cost"),
            backend_sequence=_optional_integer(value.get("backend_sequence"), "backend_sequence"),
            native_payload_ref=_optional_string(value.get("native_payload_ref")),
            extensions={key: item for key, item in value.items() if key not in _EVENT_FIELDS},
        )

    @classmethod
    def from_json(cls, value: str) -> "AgentEvent":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise TraceSchemaError(f"invalid event JSON: {exc.msg}") from exc
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            **dict(self.extensions),
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "type": self.type,
            "actor": self.actor,
            "payload": dict(self.payload),
            "evidence_refs": list(self.evidence_refs),
        }
        optional = {
            "parent_run_id": self.parent_run_id,
            "step_id": self.step_id,
            "tool_call_id": self.tool_call_id,
            "artifact_ids": list(self.artifact_ids) if self.artifact_ids else None,
            "token_usage": self.token_usage.to_dict() if self.token_usage else None,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
            "backend_sequence": self.backend_sequence,
            "native_payload_ref": self.native_payload_ref,
        }
        result.update({key: item for key, item in optional.items() if item is not None})
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    trace_id: str
    run_id: str
    type: str
    uri: str
    content_digest: str
    producer_event_id: str
    created_at: str
    media_type: str | None = None
    size: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("schema_version", "artifact_id", "trace_id", "run_id", "type", "uri", "content_digest", "producer_event_id", "created_at"):
            _non_empty(getattr(self, name), name)
        _timestamp(self.created_at)
        if self.size is not None and self.size < 0:
            raise TraceSchemaError("artifact size must be non-negative")
        _json_value(dict(self.metadata), "artifact metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArtifactRecord":
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TraceSchemaError("artifact metadata must be an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            artifact_id=str(value.get("artifact_id") or ""),
            trace_id=str(value.get("trace_id") or ""),
            run_id=str(value.get("run_id") or ""),
            type=str(value.get("type") or ""),
            uri=str(value.get("uri") or ""),
            content_digest=str(value.get("content_digest") or ""),
            producer_event_id=str(value.get("producer_event_id") or ""),
            created_at=str(value.get("created_at") or ""),
            media_type=_optional_string(value.get("media_type")),
            size=_optional_integer(value.get("size"), "size"),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "type": self.type,
            "uri": self.uri,
            "content_digest": self.content_digest,
            "producer_event_id": self.producer_event_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
        if self.media_type is not None:
            result["media_type"] = self.media_type
        if self.size is not None:
            result["size"] = self.size
        return result


@dataclass(frozen=True)
class RunHandle:
    adapter: str
    native_run_id: str
    trace_id: str
    run_id: str
    task_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("adapter", "native_run_id", "trace_id", "run_id", "task_id"):
            _non_empty(getattr(self, name), name)
        _json_value(dict(self.metadata), "run handle metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunHandle":
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TraceSchemaError("run handle metadata must be an object")
        return cls(
            adapter=str(value.get("adapter") or ""),
            native_run_id=str(value.get("native_run_id") or ""),
            trace_id=str(value.get("trace_id") or ""),
            run_id=str(value.get("run_id") or ""),
            task_id=str(value.get("task_id") or ""),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "native_run_id": self.native_run_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AdapterCapabilities:
    event_streaming: bool = False
    artifact_collection: bool = False
    context_injection: bool = False
    token_usage: bool = False
    cancellation: bool = False
    checkpointing: bool = False
    sandbox_visibility: bool = False
    subagent_visibility: bool = False
    native_event_types: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _string_tuple(self.native_event_types, "native_event_types")
        _json_value(dict(self.metadata), "capability metadata")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AdapterCapabilities":
        fields = {
            "event_streaming",
            "artifact_collection",
            "context_injection",
            "token_usage",
            "cancellation",
            "checkpointing",
            "sandbox_visibility",
            "subagent_visibility",
        }
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TraceSchemaError("capability metadata must be an object")
        return cls(
            **{name: _boolean(value.get(name, False), name) for name in fields},
            native_event_types=_strings(value.get("native_event_types"), "native_event_types"),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_streaming": self.event_streaming,
            "artifact_collection": self.artifact_collection,
            "context_injection": self.context_injection,
            "token_usage": self.token_usage,
            "cancellation": self.cancellation,
            "checkpointing": self.checkpointing,
            "sandbox_visibility": self.sandbox_visibility,
            "subagent_visibility": self.subagent_visibility,
            "native_event_types": list(self.native_event_types),
            "metadata": dict(self.metadata),
        }


def _non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TraceSchemaError(f"{name} must be a non-empty string")


def _timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TraceSchemaError(f"timestamp must be ISO-8601: {value}") from exc
    if parsed.tzinfo is None:
        raise TraceSchemaError("timestamp must include a timezone")


def _json_value(value: Any, name: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TraceSchemaError(f"{name} must be JSON serializable") from exc


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise TraceSchemaError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TraceSchemaError(f"{name} must be an integer") from exc
    if result != value and not (isinstance(value, str) and str(result) == value.strip()):
        raise TraceSchemaError(f"{name} must be an integer")
    return result


def _optional_integer(value: Any, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TraceSchemaError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TraceSchemaError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise TraceSchemaError(f"{name} must be finite")
    return result


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TraceSchemaError(f"{name} must be a boolean")
    return value


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise TraceSchemaError(f"{name} must be an array of strings")
    result = tuple(str(item) for item in value)
    _string_tuple(result, name)
    return result


def _string_tuple(value: tuple[str, ...], name: str) -> None:
    if any(not isinstance(item, str) or not item for item in value):
        raise TraceSchemaError(f"{name} must contain non-empty strings")
