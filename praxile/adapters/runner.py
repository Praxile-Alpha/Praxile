from __future__ import annotations

from dataclasses import dataclass

from ..trace import AgentEvent, ArtifactRecord, EventStore, RunHandle
from .v2 import AdapterPolicy, AdapterProtocolError, AdapterTask, AgentAdapterV2, validate_adapter_v2


@dataclass(frozen=True)
class AdapterRunResult:
    handle: RunHandle
    events: tuple[AgentEvent, ...]
    artifacts: tuple[ArtifactRecord, ...]


class AdapterRunner:
    """Executes an Adapter V2 and commits its normalized evidence to the Event Store."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def execute(self, adapter: AgentAdapterV2, task: AdapterTask, policy: AdapterPolicy) -> AdapterRunResult:
        capabilities = validate_adapter_v2(adapter)
        if policy.context and not capabilities.context_injection:
            raise AdapterProtocolError("adapter does not support the requested context injection")
        handle = adapter.run(task, policy)
        self._validate_handle(handle, adapter, task)

        events: list[AgentEvent] = []
        last_sequence: int | None = None
        for event in adapter.stream_events(handle):
            self._validate_event(event, handle)
            if event.backend_sequence is not None:
                if last_sequence is not None and event.backend_sequence < last_sequence:
                    raise AdapterProtocolError("adapter events have a decreasing backend_sequence")
                last_sequence = event.backend_sequence
            self.event_store.append(event)
            events.append(event)

        root_events = [event for event in events if event.run_id == handle.run_id]
        if not root_events or root_events[0].type != "RUN_START" or root_events[-1].type != "RUN_END":
            raise AdapterProtocolError("root run must be bounded by RUN_START and RUN_END events")

        artifacts = adapter.get_artifacts(handle)
        if artifacts and not capabilities.artifact_collection:
            raise AdapterProtocolError("adapter returned artifacts without declaring artifact_collection")
        event_ids = {event.event_id for event in events}
        for artifact in artifacts:
            self._validate_artifact(artifact, handle, event_ids)
            self.event_store.record_artifact(artifact)
        return AdapterRunResult(handle=handle, events=tuple(events), artifacts=tuple(artifacts))

    @staticmethod
    def _validate_handle(handle: RunHandle, adapter: AgentAdapterV2, task: AdapterTask) -> None:
        if not isinstance(handle, RunHandle):
            raise AdapterProtocolError("run() must return RunHandle")
        if handle.adapter != adapter.name:
            raise AdapterProtocolError("run handle adapter does not match adapter name")
        if handle.task_id != task.task_id:
            raise AdapterProtocolError("run handle task_id does not match AdapterTask")
        if handle.metadata.get("protocol_version") != adapter.protocol_version:
            raise AdapterProtocolError("run handle does not declare the adapter protocol version")

    @staticmethod
    def _validate_event(event: AgentEvent, handle: RunHandle) -> None:
        if not isinstance(event, AgentEvent):
            raise AdapterProtocolError("stream_events() must yield AgentEvent values")
        if event.trace_id != handle.trace_id or event.task_id != handle.task_id:
            raise AdapterProtocolError("event trace/task identity does not match run handle")
        if event.run_id != handle.run_id and event.parent_run_id is None:
            raise AdapterProtocolError("child-run events must declare parent_run_id")

    @staticmethod
    def _validate_artifact(artifact: ArtifactRecord, handle: RunHandle, event_ids: set[str]) -> None:
        if not isinstance(artifact, ArtifactRecord):
            raise AdapterProtocolError("get_artifacts() must return ArtifactRecord values")
        if artifact.trace_id != handle.trace_id:
            raise AdapterProtocolError("artifact trace identity does not match run handle")
        if artifact.producer_event_id not in event_ids:
            raise AdapterProtocolError("artifact producer event was not emitted by this adapter execution")
