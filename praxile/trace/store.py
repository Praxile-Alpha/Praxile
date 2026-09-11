from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..config import ProjectPaths
from ..utils import file_lock, utc_now, write_json
from .compatibility import events_to_v1_trajectory
from .schema import AgentEvent, ArtifactRecord, TraceSchemaError


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class EventStore:
    """Append-only normalized trace log with a rebuildable SQLite index."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self.root = paths.state / "trace"
        self.events_root = self.root / "events"
        self.artifacts_root = self.root / "artifacts"
        self.db_path = paths.db

    def initialize(self) -> None:
        self.events_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            self._ensure_schema(conn)

    def append(self, event: AgentEvent | dict[str, Any]) -> bool:
        return bool(self.append_many([event]))

    def append_many(self, events: Iterable[AgentEvent | dict[str, Any]]) -> list[str]:
        normalized = [item if isinstance(item, AgentEvent) else AgentEvent.from_dict(item) for item in events]
        if not normalized:
            return []
        self.initialize()
        incoming: dict[str, AgentEvent] = {}
        for event in normalized:
            self._validate_storage_ids(event)
            prior = incoming.get(event.event_id)
            if prior and prior.to_json() != event.to_json():
                raise TraceSchemaError(f"event_id collision in batch: {event.event_id}")
            incoming[event.event_id] = event

        with self._connection() as conn:
            indexed = self._indexed_payloads(conn, list(incoming))
        for event_id, payload in indexed.items():
            if payload != incoming[event_id].to_json():
                raise TraceSchemaError(f"event_id already exists with different content: {event_id}")

        appended: list[str] = []
        grouped: dict[str, list[AgentEvent]] = defaultdict(list)
        for event in incoming.values():
            grouped[event.trace_id].append(event)

        for trace_id, group in grouped.items():
            path = self._trace_path(trace_id)
            with file_lock(path):
                file_payloads = self._file_payloads(path)
                to_write: list[AgentEvent] = []
                for event in group:
                    existing = file_payloads.get(event.event_id)
                    if existing is not None:
                        if existing != event.to_json():
                            raise TraceSchemaError(f"event_id already exists in log with different content: {event.event_id}")
                        continue
                    to_write.append(event)
                if to_write:
                    with path.open("a", encoding="utf-8") as handle:
                        for event in to_write:
                            handle.write(event.to_json() + "\n")
                            appended.append(event.event_id)
                        handle.flush()

        with self._connection() as conn:
            for event in incoming.values():
                self._index_event(conn, event)
        return appended

    def list_events(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> list[AgentEvent]:
        self.initialize()
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("trace_id", trace_id), ("run_id", run_id), ("task_id", task_id), ("event_type", event_type)):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        query = "SELECT event_json FROM agent_trace_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ingestion_sequence ASC"
        with self._connection() as conn:
            rows = conn.execute(query, values).fetchall()
        return [AgentEvent.from_json(str(row["event_json"])) for row in rows]

    def get_event(self, event_id: str) -> AgentEvent | None:
        self.initialize()
        with self._connection() as conn:
            row = conn.execute("SELECT event_json FROM agent_trace_events WHERE event_id = ?", (event_id,)).fetchone()
        return AgentEvent.from_json(str(row["event_json"])) if row else None

    def record_artifact(self, artifact: ArtifactRecord | dict[str, Any]) -> Path:
        record = artifact if isinstance(artifact, ArtifactRecord) else ArtifactRecord.from_dict(artifact)
        self._safe_id(record.artifact_id, "artifact_id")
        self.initialize()
        producer = self.get_event(record.producer_event_id)
        if producer is None:
            raise TraceSchemaError(f"artifact producer event does not exist: {record.producer_event_id}")
        if producer.trace_id != record.trace_id or producer.run_id != record.run_id:
            raise TraceSchemaError("artifact trace/run identity must match its producer event")
        path = self.artifacts_root / f"{record.artifact_id}.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if current is not None and current != record.to_dict():
            raise TraceSchemaError(f"artifact_id already exists with different content: {record.artifact_id}")
        with self._connection() as conn:
            row = conn.execute("SELECT artifact_json FROM trace_artifacts WHERE artifact_id = ?", (record.artifact_id,)).fetchone()
            serialized = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if row and str(row["artifact_json"]) != serialized:
                raise TraceSchemaError(f"artifact_id index collision: {record.artifact_id}")
            if current is None:
                write_json(path, record.to_dict())
            conn.execute(
                """
                INSERT OR IGNORE INTO trace_artifacts
                (artifact_id, trace_id, run_id, artifact_type, uri, content_digest,
                 producer_event_id, media_type, size, artifact_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id,
                    record.trace_id,
                    record.run_id,
                    record.type,
                    record.uri,
                    record.content_digest,
                    record.producer_event_id,
                    record.media_type,
                    record.size,
                    serialized,
                    record.created_at,
                ),
            )
        return path

    def list_artifacts(self, trace_id: str) -> list[ArtifactRecord]:
        self.initialize()
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT artifact_json FROM trace_artifacts WHERE trace_id = ? ORDER BY created_at, artifact_id",
                (trace_id,),
            ).fetchall()
        return [ArtifactRecord.from_dict(json.loads(str(row["artifact_json"]))) for row in rows]

    def replay(self, trace_id: str) -> dict[str, Any]:
        events = self.list_events(trace_id=trace_id)
        if not events:
            raise FileNotFoundError(f"trace not found: {trace_id}")
        ordered = sorted(
            enumerate(events),
            key=lambda item: (
                item[1].timestamp,
                item[1].backend_sequence if item[1].backend_sequence is not None else 2**63,
                item[0],
            ),
        )
        events = [item[1] for item in ordered]
        runs: dict[str, dict[str, Any]] = {}
        anomalies: list[dict[str, str]] = []
        for event in events:
            run = runs.setdefault(
                event.run_id,
                {
                    "run_id": event.run_id,
                    "parent_run_id": event.parent_run_id,
                    "event_ids": [],
                    "status": "unknown",
                    "started_at": None,
                    "ended_at": None,
                },
            )
            if run["parent_run_id"] not in {None, event.parent_run_id}:
                anomalies.append({"type": "parent_mismatch", "run_id": event.run_id, "event_id": event.event_id})
            run["parent_run_id"] = run["parent_run_id"] or event.parent_run_id
            run["event_ids"].append(event.event_id)
            if event.type == "RUN_START":
                run["started_at"] = event.timestamp
            elif event.type in {"FINAL_RESULT", "RUN_END"}:
                run["status"] = str(event.payload.get("status") or run["status"])
                if event.type == "RUN_END":
                    run["ended_at"] = event.timestamp
        for run_id, run in runs.items():
            if run["started_at"] is None:
                anomalies.append({"type": "missing_run_start", "run_id": run_id, "event_id": ""})
            parent = run.get("parent_run_id")
            if parent and parent not in runs:
                anomalies.append({"type": "missing_parent_run", "run_id": run_id, "event_id": ""})
        anomalies.extend(self._cycle_anomalies(runs))
        return {
            "schema_version": "praxile.trace_replay.v1",
            "trace_id": trace_id,
            "task_ids": sorted({event.task_id for event in events}),
            "event_count": len(events),
            "events": [event.to_dict() for event in events],
            "runs": list(runs.values()),
            "artifacts": [item.to_dict() for item in self.list_artifacts(trace_id)],
            "anomalies": anomalies,
            "replayed_at": utc_now(),
        }

    def v1_projection(self, trace_id: str) -> dict[str, Any]:
        return events_to_v1_trajectory(self.list_events(trace_id=trace_id))

    def rebuild_index(self) -> dict[str, int]:
        self.initialize()
        event_count = 0
        artifact_count = 0
        with self._connection() as conn:
            conn.execute("DELETE FROM agent_trace_events")
            conn.execute("DELETE FROM trace_artifacts")
            for path in sorted(self.events_root.glob("*.jsonl")):
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        event = AgentEvent.from_json(line)
                    except TraceSchemaError as exc:
                        raise TraceSchemaError(f"{path}:{line_number}: {exc}") from exc
                    self._validate_storage_ids(event)
                    self._index_event(conn, event)
                    event_count += 1
            for path in sorted(self.artifacts_root.glob("*.json")):
                record = ArtifactRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
                producer = conn.execute(
                    "SELECT trace_id, run_id FROM agent_trace_events WHERE event_id = ?",
                    (record.producer_event_id,),
                ).fetchone()
                if producer is None:
                    raise TraceSchemaError(f"artifact producer event does not exist: {record.producer_event_id}")
                if producer["trace_id"] != record.trace_id or producer["run_id"] != record.run_id:
                    raise TraceSchemaError(f"artifact identity does not match producer event: {record.artifact_id}")
                serialized = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO trace_artifacts
                    (artifact_id, trace_id, run_id, artifact_type, uri, content_digest,
                     producer_event_id, media_type, size, artifact_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.artifact_id, record.trace_id, record.run_id, record.type, record.uri,
                        record.content_digest, record.producer_event_id, record.media_type, record.size,
                        serialized, record.created_at,
                    ),
                )
                artifact_count += 1
        return {"events": event_count, "artifacts": artifact_count}

    def _index_event(self, conn: sqlite3.Connection, event: AgentEvent) -> None:
        serialized = event.to_json()
        prior = conn.execute("SELECT event_json FROM agent_trace_events WHERE event_id = ?", (event.event_id,)).fetchone()
        if prior:
            if str(prior["event_json"]) != serialized:
                raise TraceSchemaError(f"event_id index collision: {event.event_id}")
            return
        conn.execute(
            """
            INSERT INTO agent_trace_events
            (event_id, schema_version, timestamp, trace_id, run_id, parent_run_id, task_id,
             event_type, actor, step_id, tool_call_id, event_json, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id, event.schema_version, event.timestamp, event.trace_id, event.run_id,
                event.parent_run_id, event.task_id, event.type, event.actor, event.step_id,
                event.tool_call_id, serialized, utc_now(),
            ),
        )

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_trace_events (
              ingestion_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              schema_version TEXT NOT NULL,
              timestamp TEXT NOT NULL,
              trace_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              parent_run_id TEXT,
              task_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              actor TEXT NOT NULL,
              step_id TEXT,
              tool_call_id TEXT,
              event_json TEXT NOT NULL,
              ingested_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_trace_events_trace
              ON agent_trace_events(trace_id, ingestion_sequence);
            CREATE INDEX IF NOT EXISTS idx_agent_trace_events_run
              ON agent_trace_events(run_id, ingestion_sequence);
            CREATE INDEX IF NOT EXISTS idx_agent_trace_events_task
              ON agent_trace_events(task_id, ingestion_sequence);
            CREATE INDEX IF NOT EXISTS idx_agent_trace_events_type
              ON agent_trace_events(event_type, timestamp);
            CREATE TABLE IF NOT EXISTS trace_artifacts (
              artifact_id TEXT PRIMARY KEY,
              trace_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              artifact_type TEXT NOT NULL,
              uri TEXT NOT NULL,
              content_digest TEXT NOT NULL,
              producer_event_id TEXT NOT NULL,
              media_type TEXT,
              size INTEGER,
              artifact_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trace_artifacts_trace
              ON trace_artifacts(trace_id, created_at);
            """
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _trace_path(self, trace_id: str) -> Path:
        self._safe_id(trace_id, "trace_id")
        return self.events_root / f"{trace_id}.jsonl"

    @staticmethod
    def _safe_id(value: str, name: str) -> None:
        if not _SAFE_ID.fullmatch(value):
            raise TraceSchemaError(f"{name} is not safe for local trace storage: {value!r}")

    def _validate_storage_ids(self, event: AgentEvent) -> None:
        for name in ("event_id", "trace_id", "run_id", "task_id"):
            self._safe_id(str(getattr(event, name)), name)
        if event.parent_run_id:
            self._safe_id(event.parent_run_id, "parent_run_id")
        if event.tool_call_id:
            self._safe_id(event.tool_call_id, "tool_call_id")

    @staticmethod
    def _file_payloads(path: Path) -> dict[str, str]:
        rows: dict[str, str] = {}
        if not path.exists():
            return rows
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            event = AgentEvent.from_json(line)
            serialized = event.to_json()
            prior = rows.get(event.event_id)
            if prior is not None and prior != serialized:
                raise TraceSchemaError(f"{path}:{line_number}: conflicting duplicate event_id {event.event_id}")
            rows[event.event_id] = serialized
        return rows

    @staticmethod
    def _indexed_payloads(conn: sqlite3.Connection, event_ids: list[str]) -> dict[str, str]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        rows = conn.execute(
            f"SELECT event_id, event_json FROM agent_trace_events WHERE event_id IN ({placeholders})",
            event_ids,
        ).fetchall()
        return {str(row["event_id"]): str(row["event_json"]) for row in rows}

    @staticmethod
    def _cycle_anomalies(runs: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
        anomalies: list[dict[str, str]] = []
        for run_id in runs:
            seen: set[str] = set()
            cursor: str | None = run_id
            while cursor and cursor in runs:
                if cursor in seen:
                    anomalies.append({"type": "parent_cycle", "run_id": run_id, "event_id": ""})
                    break
                seen.add(cursor)
                cursor = runs[cursor].get("parent_run_id")
        return anomalies
