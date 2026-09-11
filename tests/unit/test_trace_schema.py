from __future__ import annotations

import pytest

from praxile.trace import (
    AdapterCapabilities,
    AgentEvent,
    ArtifactRecord,
    RunHandle,
    TokenUsage,
    TraceIdentity,
    TraceSchemaError,
)


def test_agent_event_round_trip_preserves_unknown_type_and_extensions() -> None:
    payload = {
        "schema_version": "praxile.agent_event.v1",
        "event_id": "event_001",
        "timestamp": "2026-09-11T08:00:00+00:00",
        "trace_id": "trace_001",
        "run_id": "run_001",
        "task_id": "task_001",
        "type": "BACKEND_FUTURE_EVENT",
        "actor": "agent",
        "payload": {"native": {"answer": 42}},
        "evidence_refs": ["artifact_001"],
        "token_usage": {"input": 12, "output": 3, "cache": 2, "reasoning": 1},
        "backend_extension": {"sequence_kind": "delta"},
    }

    event = AgentEvent.from_dict(payload)
    restored = AgentEvent.from_json(event.to_json())

    assert event.is_known_type is False
    assert restored.to_dict() == event.to_dict()
    assert restored.extensions["backend_extension"]["sequence_kind"] == "delta"
    assert restored.token_usage is not None
    assert restored.token_usage.extensions["reasoning"] == 1


@pytest.mark.parametrize(
    "change, message",
    [
        ({"event_id": ""}, "event_id"),
        ({"timestamp": "2026-09-11"}, "timezone"),
        ({"payload": []}, "payload"),
        ({"latency_ms": -1}, "latency_ms"),
        ({"parent_run_id": "run_001"}, "parent_run_id"),
    ],
)
def test_agent_event_rejects_invalid_envelope(change: dict[str, object], message: str) -> None:
    payload: dict[str, object] = {
        "schema_version": "praxile.agent_event.v1",
        "event_id": "event_001",
        "timestamp": "2026-09-11T08:00:00+00:00",
        "trace_id": "trace_001",
        "run_id": "run_001",
        "task_id": "task_001",
        "type": "RUN_START",
        "actor": "agent",
        "payload": {},
        "evidence_refs": [],
    }
    payload.update(change)

    with pytest.raises(TraceSchemaError, match=message):
        AgentEvent.from_dict(payload)


def test_token_usage_rejects_negative_values() -> None:
    with pytest.raises(TraceSchemaError, match="non-negative"):
        TokenUsage(input=-1)


def test_trace_identity_is_explicit_and_matches_event() -> None:
    identity = TraceIdentity(task_id="task_001", trace_id="trace_001", run_id="run_001")
    event = AgentEvent(
        event_id="event_001",
        timestamp="2026-09-11T08:00:00+00:00",
        type="RUN_START",
        actor="agent",
        payload={},
        **identity.to_dict(),
    )

    assert TraceIdentity.from_dict(identity.to_dict()) == identity
    assert event.identity == identity


def test_artifact_and_capability_schema_round_trip() -> None:
    artifact = ArtifactRecord(
        artifact_id="artifact_patch_1",
        trace_id="trace_001",
        run_id="run_001",
        type="patch",
        uri="artifacts/fix.patch",
        content_digest="sha256:abc",
        producer_event_id="event_patch_1",
        created_at="2026-09-11T08:00:00+00:00",
        media_type="text/x-diff",
        size=120,
        metadata={"redacted": False},
    )
    capabilities = AdapterCapabilities(
        event_streaming=True,
        artifact_collection=True,
        cancellation=True,
        native_event_types=("tool_call", "tool_result"),
        metadata={"adapter_version": "1"},
    )
    handle = RunHandle(
        adapter="fixture",
        native_run_id="native-1",
        trace_id="trace_001",
        run_id="run_001",
        task_id="task_001",
        metadata={"pid": 42},
    )

    assert ArtifactRecord.from_dict(artifact.to_dict()) == artifact
    assert AdapterCapabilities.from_dict(capabilities.to_dict()) == capabilities
    assert RunHandle.from_dict(handle.to_dict()) == handle


def test_capability_schema_rejects_string_booleans() -> None:
    with pytest.raises(TraceSchemaError, match="boolean"):
        AdapterCapabilities.from_dict({"event_streaming": "false"})
