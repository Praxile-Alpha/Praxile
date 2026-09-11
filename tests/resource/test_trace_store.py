from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from praxile.config import Config
from praxile.trace import AgentEvent, ArtifactRecord, EventStore, TraceSchemaError


pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


def _event(event_id: str, event_type: str, *, run_id: str = "run_root", parent_run_id: str | None = None) -> AgentEvent:
    return AgentEvent(
        event_id=event_id,
        timestamp="2026-09-11T08:00:00+00:00",
        trace_id="trace_store_1",
        run_id=run_id,
        parent_run_id=parent_run_id,
        task_id="task_store_1",
        type=event_type,
        actor="agent",
        payload={"status": "completed"} if event_type in {"FINAL_RESULT", "RUN_END"} else {},
        backend_sequence={"RUN_START": 0, "SUBAGENT_START": 1, "RUN_END": 2}.get(event_type),
    )


def test_event_store_append_is_idempotent_and_rebuildable(tmp_path: Path) -> None:
    store = EventStore(Config.load(tmp_path).paths)
    start = _event("event_start", "RUN_START")
    end = _event("event_end", "RUN_END")

    assert store.append(start) is True
    assert store.append(start) is False
    assert store.append(end) is True
    assert len((store.events_root / "trace_store_1.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert [item.event_id for item in store.list_events(trace_id="trace_store_1")] == ["event_start", "event_end"]

    log_path = store.events_root / "trace_store_1.jsonl"
    log_path.unlink()
    assert store.append(start) is True
    assert log_path.read_text(encoding="utf-8").count("event_start") == 1

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DELETE FROM agent_trace_events")
        conn.commit()
    assert store.list_events(trace_id="trace_store_1") == []
    assert store.rebuild_index() == {"events": 1, "artifacts": 0}
    assert [item.event_id for item in store.list_events(trace_id="trace_store_1")] == ["event_start"]


def test_event_store_replay_builds_parent_child_run_graph(tmp_path: Path) -> None:
    store = EventStore(Config.load(tmp_path).paths)
    events = [
        _event("event_root_start", "RUN_START"),
        _event("event_child_start", "RUN_START", run_id="run_child", parent_run_id="run_root"),
        _event("event_child_end", "RUN_END", run_id="run_child", parent_run_id="run_root"),
        _event("event_root_end", "RUN_END"),
    ]
    store.append_many(events)

    replay = store.replay("trace_store_1")

    assert replay["event_count"] == 4
    assert replay["anomalies"] == []
    child = next(item for item in replay["runs"] if item["run_id"] == "run_child")
    assert child["parent_run_id"] == "run_root"
    assert child["status"] == "completed"


def test_event_store_records_artifact_against_producer_event(tmp_path: Path) -> None:
    store = EventStore(Config.load(tmp_path).paths)
    producer = _event("event_artifact", "ARTIFACT_CHANGE")
    store.append(producer)
    artifact = ArtifactRecord(
        artifact_id="artifact_patch",
        trace_id=producer.trace_id,
        run_id=producer.run_id,
        type="patch",
        uri="artifacts/fix.patch",
        content_digest="sha256:abc",
        producer_event_id=producer.event_id,
        created_at=producer.timestamp,
        media_type="text/x-diff",
    )

    path = store.record_artifact(artifact)

    assert path.exists()
    assert store.list_artifacts(producer.trace_id) == [artifact]
    assert store.replay(producer.trace_id)["artifacts"][0]["artifact_id"] == "artifact_patch"


def test_event_store_rejects_path_escaping_trace_id(tmp_path: Path) -> None:
    store = EventStore(Config.load(tmp_path).paths)
    event = AgentEvent.from_dict({**_event("event_bad", "RUN_START").to_dict(), "trace_id": "../escape"})

    with pytest.raises(TraceSchemaError, match="not safe"):
        store.append(event)
