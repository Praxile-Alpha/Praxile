from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping

from ..trace import AdapterCapabilities, AgentEvent, ArtifactRecord, RunHandle
from ..utils import new_id, utc_now
from .v2 import (
    AGENT_ADAPTER_PROTOCOL_VERSION,
    AdapterPolicy,
    AdapterRunNotFound,
    AdapterTask,
)


@dataclass(frozen=True)
class FixtureEvent:
    type: str
    actor: str = "fixture-agent"
    payload: Mapping[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class FixtureArtifact:
    type: str
    uri: str
    content_digest: str
    media_type: str | None = None
    size: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class _FixtureRun:
    task: AdapterTask
    policy: AdapterPolicy
    events: list[AgentEvent]
    artifacts: list[ArtifactRecord]
    cancelled: bool = False
    completed: bool = False


class FixtureAgentAdapter:
    """Deterministic in-process adapter used for protocol and control-plane tests."""

    name = "fixture"
    protocol_version = AGENT_ADAPTER_PROTOCOL_VERSION

    def __init__(
        self,
        events: list[FixtureEvent] | None = None,
        artifacts: list[FixtureArtifact] | None = None,
    ):
        self._event_specs = list(events or [])
        self._artifact_specs = list(artifacts or [])
        self._runs: dict[str, _FixtureRun] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            event_streaming=True,
            artifact_collection=True,
            context_injection=True,
            token_usage=True,
            cancellation=True,
            checkpointing=True,
            sandbox_visibility=True,
            subagent_visibility=True,
            native_event_types=tuple(sorted({item.type for item in self._event_specs})),
            metadata={
                "protocol_version": self.protocol_version,
                "adapter_version": "1",
                "deterministic": True,
            },
        )

    def run(self, task: AdapterTask, policy: AdapterPolicy) -> RunHandle:
        native_run_id = new_id("fixture-native")
        trace_id = str(task.metadata.get("trace_id") or new_id("trace"))
        run_id = str(task.metadata.get("run_id") or new_id("run"))
        handle = RunHandle(
            adapter=self.name,
            native_run_id=native_run_id,
            trace_id=trace_id,
            run_id=run_id,
            task_id=task.task_id,
            metadata={"protocol_version": self.protocol_version, "policy_id": policy.policy_id},
        )
        events = [self._event(handle, 0, "RUN_START", "fixture-runtime", {"instruction": task.instruction})]
        sequence = 1
        if policy.context:
            events.append(
                self._event(
                    handle,
                    sequence,
                    "CONTEXT_INJECT",
                    "praxile-control-plane",
                    {
                        "policy_id": policy.policy_id,
                        "policy_version": policy.version,
                        "items": [dict(item) for item in policy.context],
                        "budgets": dict(policy.budgets),
                        "settings": dict(policy.settings),
                    },
                )
            )
            sequence += 1
        for spec in self._event_specs:
            events.append(
                self._event(
                    handle,
                    sequence,
                    spec.type,
                    spec.actor,
                    dict(spec.payload),
                    step_id=spec.step_id,
                    tool_call_id=spec.tool_call_id,
                )
            )
            sequence += 1

        artifacts: list[ArtifactRecord] = []
        for spec in self._artifact_specs:
            artifact_id = new_id("artifact")
            producer = self._event(
                handle,
                sequence,
                "ARTIFACT_CHANGE",
                "fixture-agent",
                {"artifact_type": spec.type, "uri": spec.uri},
                artifact_ids=(artifact_id,),
            )
            events.append(producer)
            artifacts.append(
                ArtifactRecord(
                    artifact_id=artifact_id,
                    trace_id=handle.trace_id,
                    run_id=handle.run_id,
                    type=spec.type,
                    uri=spec.uri,
                    content_digest=spec.content_digest,
                    producer_event_id=producer.event_id,
                    created_at=producer.timestamp,
                    media_type=spec.media_type,
                    size=spec.size,
                    metadata=dict(spec.metadata),
                )
            )
            sequence += 1
        events.extend(
            [
                self._event(handle, sequence, "FINAL_RESULT", "fixture-agent", {"status": "completed"}),
                self._event(handle, sequence + 1, "RUN_END", "fixture-runtime", {"status": "completed"}),
            ]
        )
        self._runs[native_run_id] = _FixtureRun(task=task, policy=policy, events=events, artifacts=artifacts)
        return handle

    def stream_events(self, run_handle: RunHandle) -> Iterator[AgentEvent]:
        state = self._state(run_handle)
        if state.cancelled:
            yield self._event(run_handle, 0, "RUN_START", "fixture-runtime", {"instruction": state.task.instruction})
            yield self._event(run_handle, 1, "FINAL_RESULT", "fixture-agent", {"status": "cancelled"})
            yield self._event(run_handle, 2, "RUN_END", "fixture-runtime", {"status": "cancelled"})
            state.completed = True
            return
        yield from state.events
        state.completed = True

    def get_artifacts(self, run_handle: RunHandle) -> list[ArtifactRecord]:
        state = self._state(run_handle)
        return [] if state.cancelled else list(state.artifacts)

    def cancel(self, run_handle: RunHandle) -> None:
        state = self._state(run_handle)
        if not state.completed:
            state.cancelled = True

    def _state(self, handle: RunHandle) -> _FixtureRun:
        state = self._runs.get(handle.native_run_id)
        if state is None or handle.adapter != self.name:
            raise AdapterRunNotFound(f"unknown fixture run: {handle.native_run_id}")
        return state

    @staticmethod
    def _event(
        handle: RunHandle,
        sequence: int,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ) -> AgentEvent:
        return AgentEvent.create(
            event_id=f"{handle.run_id}:fixture:{sequence}",
            timestamp=utc_now(),
            trace_id=handle.trace_id,
            run_id=handle.run_id,
            task_id=handle.task_id,
            type=event_type,
            actor=actor,
            payload=payload,
            backend_sequence=sequence,
            **kwargs,
        )
