from __future__ import annotations

from pathlib import Path

import pytest

from praxile.adapters import (
    AGENT_ADAPTER_PROTOCOL_VERSION,
    AdapterPolicy,
    AdapterProtocolError,
    AdapterTask,
    FixtureAgentAdapter,
    FixtureArtifact,
    FixtureEvent,
    validate_adapter_v2,
)


def test_adapter_task_and_policy_round_trip(tmp_path: Path) -> None:
    task = AdapterTask(
        task_id="task_fixture",
        instruction="Fix the parser",
        project_root=str(tmp_path),
        metadata={"trace_id": "trace_fixture"},
    )
    policy = AdapterPolicy(
        policy_id="candidate",
        version="2",
        context=({"type": "skill", "id": "skill_parser"},),
        budgets={"max_steps": 8},
        settings={"workspace_isolated": True},
    )

    assert AdapterTask.from_dict(task.to_dict()) == task
    assert AdapterPolicy.from_dict(policy.to_dict()) == policy


def test_fixture_adapter_conforms_and_emits_normalized_context(tmp_path: Path) -> None:
    adapter = FixtureAgentAdapter(
        events=[
            FixtureEvent("MODEL_CALL", payload={"model": "fixture-model"}),
            FixtureEvent("TOOL_CALL", payload={"tool": "read_file"}, tool_call_id="call_1"),
            FixtureEvent("TOOL_RESULT", payload={"ok": True}, tool_call_id="call_1"),
        ],
        artifacts=[FixtureArtifact("patch", "fixture://change.patch", "sha256:fixture")],
    )
    capabilities = validate_adapter_v2(adapter)
    task = AdapterTask("task_fixture", "Fix it", str(tmp_path))
    policy = AdapterPolicy(context=({"asset_id": "skill_1", "content": "Run focused tests"},))

    handle = adapter.run(task, policy)
    events = list(adapter.stream_events(handle))
    artifacts = adapter.get_artifacts(handle)

    assert handle.metadata["protocol_version"] == AGENT_ADAPTER_PROTOCOL_VERSION
    assert capabilities.event_streaming is True
    assert [event.backend_sequence for event in events] == list(range(len(events)))
    context_event = next(event for event in events if event.type == "CONTEXT_INJECT")
    assert context_event.payload["policy_version"] == policy.version
    assert context_event.payload["budgets"] == policy.budgets
    assert context_event.payload["settings"] == policy.settings
    assert events[0].type == "RUN_START"
    assert events[-1].type == "RUN_END"
    assert artifacts[0].producer_event_id in {event.event_id for event in events}


def test_fixture_cancel_is_idempotent_and_suppresses_artifacts(tmp_path: Path) -> None:
    adapter = FixtureAgentAdapter(artifacts=[FixtureArtifact("patch", "fixture://patch", "sha256:x")])
    handle = adapter.run(AdapterTask("task_cancel", "Stop", str(tmp_path)), AdapterPolicy())

    adapter.cancel(handle)
    adapter.cancel(handle)

    assert [event.payload.get("status") for event in adapter.stream_events(handle) if event.type == "RUN_END"] == [
        "cancelled"
    ]
    assert adapter.get_artifacts(handle) == []


def test_fixture_cancel_after_completion_does_not_rewrite_history(tmp_path: Path) -> None:
    adapter = FixtureAgentAdapter(artifacts=[FixtureArtifact("patch", "fixture://patch", "sha256:x")])
    handle = adapter.run(AdapterTask("task_complete", "Finish", str(tmp_path)), AdapterPolicy())
    original = list(adapter.stream_events(handle))

    adapter.cancel(handle)

    assert list(adapter.stream_events(handle)) == original
    assert len(adapter.get_artifacts(handle)) == 1


def test_adapter_validator_rejects_wrong_protocol_version() -> None:
    adapter = FixtureAgentAdapter()
    adapter.protocol_version = "praxile.agent_adapter.v99"

    with pytest.raises(AdapterProtocolError, match="unsupported adapter protocol"):
        validate_adapter_v2(adapter)
