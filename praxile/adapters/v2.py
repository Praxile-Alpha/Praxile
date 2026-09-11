from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable

from ..trace import AdapterCapabilities, AgentEvent, ArtifactRecord, RunHandle


AGENT_ADAPTER_PROTOCOL_VERSION = "praxile.agent_adapter.v2"


class AdapterError(RuntimeError):
    """Base error raised at the external-agent boundary."""


class AdapterUnavailableError(AdapterError):
    """Raised when an adapter's optional runtime is not installed or reachable."""


class AdapterPolicyError(AdapterError):
    """Raised when a run would violate the supplied harness policy."""


class AdapterRunNotFound(AdapterError):
    """Raised when a run handle is unknown to an adapter instance."""


class AdapterProtocolError(AdapterError):
    """Raised when an adapter violates the versioned V2 contract."""


@dataclass(frozen=True)
class AdapterTask:
    task_id: str
    instruction: str
    project_root: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty(self.task_id, "task_id")
        _non_empty(self.instruction, "instruction")
        _non_empty(self.project_root, "project_root")
        _json_object(self.metadata, "task metadata")

    @property
    def root(self) -> Path:
        return Path(self.project_root).expanduser().resolve()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AdapterTask":
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise AdapterProtocolError("task metadata must be an object")
        return cls(
            task_id=str(value.get("task_id") or ""),
            instruction=str(value.get("instruction") or ""),
            project_root=str(value.get("project_root") or ""),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "instruction": self.instruction,
            "project_root": self.project_root,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AdapterPolicy:
    policy_id: str = "default"
    version: str = "1"
    context: tuple[Mapping[str, Any], ...] = ()
    budgets: Mapping[str, Any] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _non_empty(self.policy_id, "policy_id")
        _non_empty(self.version, "version")
        for index, item in enumerate(self.context):
            _json_object(item, f"context[{index}]")
        _json_object(self.budgets, "policy budgets")
        _json_object(self.settings, "policy settings")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AdapterPolicy":
        raw_context = value.get("context", [])
        if not isinstance(raw_context, list):
            raise AdapterProtocolError("policy context must be an array")
        context: list[Mapping[str, Any]] = []
        for index, item in enumerate(raw_context):
            if not isinstance(item, Mapping):
                raise AdapterProtocolError(f"context[{index}] must be an object")
            context.append(dict(item))
        budgets = value.get("budgets", {})
        settings = value.get("settings", {})
        if not isinstance(budgets, Mapping) or not isinstance(settings, Mapping):
            raise AdapterProtocolError("policy budgets and settings must be objects")
        return cls(
            policy_id=str(value.get("policy_id") or ""),
            version=str(value.get("version") or ""),
            context=tuple(context),
            budgets=dict(budgets),
            settings=dict(settings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "context": [dict(item) for item in self.context],
            "budgets": dict(self.budgets),
            "settings": dict(self.settings),
        }


@runtime_checkable
class AgentAdapterV2(Protocol):
    """Transport-neutral execution protocol for external agent runtimes."""

    name: str
    protocol_version: str

    def run(self, task: AdapterTask, policy: AdapterPolicy) -> RunHandle:
        """Start a native run and return its stable Praxile identity."""

    def stream_events(self, run_handle: RunHandle) -> Iterator[AgentEvent]:
        """Yield normalized events in adapter-observed order."""

    def get_artifacts(self, run_handle: RunHandle) -> list[ArtifactRecord]:
        """Return artifacts whose producer events were emitted by this run."""

    def cancel(self, run_handle: RunHandle) -> None:
        """Request cancellation. Repeated cancellation must be safe."""

    def capabilities(self) -> AdapterCapabilities:
        """Declare observable support; unsupported features must not be inferred."""


def validate_adapter_v2(adapter: object) -> AdapterCapabilities:
    if not isinstance(adapter, AgentAdapterV2):
        raise AdapterProtocolError("adapter does not implement all AgentAdapter V2 methods")
    if getattr(adapter, "protocol_version", None) != AGENT_ADAPTER_PROTOCOL_VERSION:
        raise AdapterProtocolError(
            f"unsupported adapter protocol: {getattr(adapter, 'protocol_version', None)!r}; "
            f"expected {AGENT_ADAPTER_PROTOCOL_VERSION!r}"
        )
    name = getattr(adapter, "name", None)
    _non_empty(name, "adapter name")
    capabilities = adapter.capabilities()
    if not isinstance(capabilities, AdapterCapabilities):
        raise AdapterProtocolError("capabilities() must return AdapterCapabilities")
    declared = capabilities.metadata.get("protocol_version")
    if declared != AGENT_ADAPTER_PROTOCOL_VERSION:
        raise AdapterProtocolError("capability metadata must declare the AgentAdapter V2 protocol version")
    return capabilities


def _non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AdapterProtocolError(f"{name} must be a non-empty string")


def _json_object(value: Mapping[str, Any], name: str) -> None:
    if not isinstance(value, Mapping):
        raise AdapterProtocolError(f"{name} must be an object")
    try:
        json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AdapterProtocolError(f"{name} must be JSON serializable: {exc}") from exc
