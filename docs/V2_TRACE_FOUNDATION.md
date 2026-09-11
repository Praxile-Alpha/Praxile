# Praxile V2 Trace Foundation

Status: P0-A schema and local event-store foundation implemented  
Last updated: 2026-09-11

## Purpose

The V2 Trace Foundation provides a backend-neutral audit contract before Praxile connects to a real external agent. It deliberately does not start processes, inject context, or implement agent policy.

## Public Python surface

`praxile.trace` exports:

- `TraceIdentity`
- `AgentEvent`
- `TokenUsage`
- `ArtifactRecord`
- `RunHandle`
- `AdapterCapabilities`
- `EventStore`
- `v1_trajectory_to_events`
- `events_to_v1_trajectory`

All schema objects use explicit validation and JSON-compatible dictionaries. `AgentEvent` preserves unknown event types and unknown envelope extensions so a newer adapter record can survive an older Praxile read/write cycle.

## Storage layout

```text
.praxile/
├── trace/
│   ├── events/
│   │   └── <trace_id>.jsonl
│   └── artifacts/
│       └── <artifact_id>.json
└── db/
    └── index.sqlite
```

The JSONL stream is canonical and append-only. SQLite tables `agent_trace_events` and `trace_artifacts` are query indexes and can be rebuilt from the files. Event ingestion is idempotent by `event_id`; reusing an ID with different content is rejected.

Artifact metadata is immutable by `artifact_id` and must reference an existing producer event with the same trace and run identity. Large artifact payloads remain at the recorded URI rather than being copied into SQLite.

## Replay

`EventStore.replay(trace_id)` reconstructs:

- ordered events;
- run and parent-run relationships;
- run start, end, and status;
- linked artifact metadata;
- structural anomalies such as missing starts, missing parents, parent mismatches, and cycles.

Replay ordering uses timestamp, backend sequence when supplied, and ingestion order as the final stable tie-breaker. A V1-compatible read projection can be produced without changing the canonical stream.

## V1 compatibility

`v1_trajectory_to_events` deterministically converts the current task-level trajectory into normalized events. Re-importing identical content produces identical event IDs. The source trajectory is never rewritten.

The reverse projection preserves the task, environment snapshot, loaded assets, plan, tool calls and observations, artifacts, diff summary, reward, candidates, and final result needed by current consumers. It is a compatibility read model, not a byte-for-byte round trip. Backend-private fields with no normalized representation remain available only through event payloads or native references.

## Safety and integrity

- Trace and artifact IDs used as filenames are restricted to storage-safe characters.
- Event and artifact writes use project-local paths under `.praxile/trace/`.
- Append operations use the existing cross-platform file lock.
- JSONL is checked before append, so conflicting duplicate IDs cannot corrupt the stream.
- Index rebuilding rejects malformed events and orphaned or identity-mismatched artifacts.
- Numeric costs, latency, sequence, size, and token counts reject invalid negative or non-finite values.

## Not implemented yet

- the versioned execution `AgentAdapter` protocol;
- mini-SWE-agent capability detection and process lifecycle;
- live event streaming from an external backend;
- automatic projection of new V1 runs into the V2 store;
- a real repository task with context, patch, verification, and result artifacts;
- trace CLI commands and benchmark integration.

These remain unchecked in the frozen P0-A checklist. No current `praxile run` behavior has changed.

## Verification

Pure schema and compatibility tests live in:

```text
tests/unit/test_trace_schema.py
tests/unit/test_trace_compatibility.py
```

SQLite and append-log tests are explicitly marked `resource` and `sqlite_resource`:

```text
tests/resource/test_trace_store.py
```

