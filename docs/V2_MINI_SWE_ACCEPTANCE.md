# Praxile V2 mini-SWE-agent Acceptance

Status: accepted for P0-A adapter and trace foundation

Execution date: 2026-09-11
mini-SWE-agent version: 2.4.6

## Scope

This acceptance run verified the real mini-SWE-agent CLI, agent loop, local environment, command execution, native trajectory serialization, Praxile `AgentAdapterV2`, normalized Event Store ingestion, artifact integrity, and SQLite replay.

The run used mini-SWE-agent's deterministic model in an isolated temporary Git repository. This deliberately removes cloud credentials, model variance, and cost from infrastructure acceptance. It validates execution and evidence fidelity, not external-model coding quality or SWE-bench performance.

## Task

The isolated repository contained a `clamp(value, lower, upper)` implementation with reversed min/max bounds and three `unittest` cases. The native mini-SWE-agent run:

1. inspected implementation and tests;
2. reproduced two failing tests;
3. edited the tracked source file;
4. reran the suite with all three tests passing;
5. inspected the Git diff;
6. emitted mini-SWE-agent's completion signal.

A single Praxile context item required failure reproduction before editing and full verification afterward. Exact policy-declared test commands were normalized as `VERIFICATION` events.

## Accepted trace

```json
{
  "trace_id": "trace_mini_swe_real_004",
  "run_id": "run_mini_swe_real_004",
  "protocol_version": "praxile.agent_adapter.v2",
  "native_exit_status": "Submitted",
  "final_status": "completed",
  "event_count": 27,
  "sqlite_event_count": 27,
  "artifact_count": 4,
  "sqlite_artifact_count": 4,
  "verification_statuses": ["failed", "passed"],
  "tests": "3 passed",
  "artifact_digests_verified": 4,
  "replay_anomalies": []
}
```

The normalized stream contains:

- `RUN_START` and `RUN_END`;
- one `CONTEXT_INJECT`;
- six `MODEL_CALL` and six `TOOL_CALL` events;
- five `TOOL_RESULT` events, with the completion action represented by the native exit record;
- two `VERIFICATION` events showing the pre-fix failure and post-fix pass;
- four `ARTIFACT_CHANGE` events;
- one evidence-linked `FINAL_RESULT`.

Artifacts retained by the isolated run:

- native mini-SWE-agent `trajectory.traj.json`;
- tracked Git `workspace.patch`;
- process stdout;
- process stderr.

Every artifact SHA-256 digest was recomputed from disk and matched its `ArtifactRecord`. Event and artifact counts matched their SQLite indexes, and `EventStore.replay()` produced a completed root run with no structural anomalies.

## Defects found during acceptance

Two failed runs were preserved as failed traces and led to adapter fixes:

1. mini-SWE-agent's first-run setup prompt blocked non-interactive execution. The adapter now defaults `MSWEA_CONFIGURED=true`, silences startup output, and isolates `MSWEA_GLOBAL_CONFIG_DIR` inside the run directory unless explicitly overridden.
2. mini-SWE-agent 2.4.6 resolves file configs using a `.yaml` suffix. The adapter now rejects an existing non-YAML config before launching the subprocess and returns a direct policy error.

These failures were not rewritten as successful runs. Their stdout/stderr remained independently hashed artifacts.

## Remaining boundary

This closes the P0-A reference-adapter and real-task trace acceptance item. It does not close P0-B or later deliverables. A fixed external model, repository preparation, SWE-bench development subset, official evaluator, resumability, reproducibility manifest, baseline/candidate comparison, and benchmark report remain required.

## Local-model follow-up

An additional routing probe exercised installed Ollama models without changing the accepted deterministic result:

- `deepseek-coder:6.7b` reached Ollama but repeatedly violated mini-SWE-agent's one-action protocol, ending as `RepeatedFormatError` even with a concise prompt;
- `gemma4:latest` could not load its local model blob and ended as `InternalServerError`;
- `deepseek-r1:7b` passed a relaxed `bash`/`mswea_bash_command` format probe but generated invalid shell actions during the repository task and reached the 300-second adapter timeout without editing the source.

All runs remained isolated and were recorded as failed or timed out rather than promoted. They demonstrate that adapter availability is not equivalent to model-role fitness. A future routing baseline should reject these local model/profile combinations for autonomous coding until a calibrated action-compliance eval passes.
