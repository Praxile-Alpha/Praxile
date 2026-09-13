# Praxile V2 P0 Frozen Boundary

Status: frozen  
Effective date: 2026-09-11  
Decision type: architecture boundary

Implementation details for completed Trace Foundation items are documented in [V2 Trace Foundation](V2_TRACE_FOUNDATION.md).

## Decision

Praxile V2 is an Eval and Evolution Control Plane for Agent Harnesses. It observes external execution, evaluates outcomes, diagnoses failures, governs reusable assets, proposes harness-policy candidates, compares candidates with baselines, and controls promotion and rollback.

Praxile V2 Core does not own a general coding-agent loop, shell runtime, sandbox, IDE, model provider product, or subagent execution engine. Those belong to an external Execution Plane and are connected through explicit adapters.

V1 remains supported as a compatibility and reference integration while V2 is built. This decision prohibits using V1 runtime expansion as a shortcut for a missing V2 adapter or trace contract.

## Root problem

The P0 goal is to answer one falsifiable question:

> With the same base agent, model, tools, task set, budget, and timeout, does one explicitly versioned Praxile policy improve success, efficiency, or reliability, and can the trace explain gains and regressions?

Any P0 work that cannot contribute evidence to this question is non-blocking or out of scope.

## Plane ownership

### External Execution Plane owns

- model invocation and agent loop;
- shell, tools, sandbox, repository mutation, and process lifecycle;
- native checkpoint and subagent execution when supported;
- backend-native sessions, prompts, and logs.

### Praxile V2 Control Plane owns

- normalized trace and artifact identity;
- adapter capability negotiation and event translation;
- experiment manifests and comparable policy variants;
- task, trajectory, system, cost, and reliability evaluation;
- evidence-linked failure diagnosis;
- governed Rule, Skill, Failure Pattern, Eval Case, Context Policy, and Recovery Strategy assets;
- candidate creation, validation, human promotion, monitoring, and rollback;
- Clean and Continual evaluation-state boundaries.

### Adapter owns

- starting, observing, cancelling, and collecting artifacts from one backend;
- translating backend-native records into normalized events;
- declaring unsupported capabilities explicitly;
- preserving native payloads as optional evidence without leaking backend policy into core services.

An adapter must not silently implement retrieval, context selection, retry, routing, or evaluation decisions that belong to a named Praxile policy.

## Frozen P0 contracts

### 1. Identity hierarchy

```text
task_id
  trace_id
    run_id
      parent_run_id optional
      step_id
        tool_call_id optional
        artifact_id optional
```

- A task is the benchmark or user problem.
- A trace is one causally connected execution tree for that task.
- A run is one agent or subagent execution.
- Parent-child relationships form an acyclic run graph.
- IDs are globally unique within a Praxile state root and never derived solely from list position.

### 2. Normalized event envelope

Every canonical event must provide:

```text
schema_version
event_id
timestamp
trace_id
run_id
task_id
type
actor
payload
evidence_refs
```

It may additionally provide `parent_run_id`, `step_id`, `tool_call_id`, `artifact_ids`, token usage, latency, cost, backend sequence, and native payload reference.

P0 event types are frozen to:

```text
RUN_START
MODEL_CALL
CONTEXT_INJECT
TOOL_CALL
TOOL_RESULT
ARTIFACT_CHANGE
VERIFICATION
CHECKPOINT
SUBAGENT_START
SUBAGENT_END
FINAL_RESULT
RUN_END
```

Adding an event type is backward compatible only when unknown types can be preserved and replayed without data loss. Renaming or changing required semantics reopens architecture review.

### 3. Canonical trace rule

The append-only normalized event stream is the V2 source of truth. Run summaries and V1-compatible trajectories are deterministic projections or imports. They must not contain facts that cannot be linked back to an event, artifact, evaluator result, user decision, or explicitly marked inference.

Ingestion must be idempotent by `event_id`. Replay ordering must use backend sequence when available and otherwise use timestamp plus ingestion sequence. Late events remain visible and trigger projection refresh; they are not silently discarded.

### 4. Adapter protocol semantics

The V2 adapter boundary must support the semantic equivalent of:

```python
run(task, policy) -> RunHandle
stream_events(run_handle) -> Iterator[AgentEvent]
get_artifacts(run_handle) -> list[Artifact]
cancel(run_handle) -> None
capabilities() -> AdapterCapabilities
```

The exact sync, async, subprocess, SDK, or RPC transport is deliberately not frozen in P0-A. Capability declarations are mandatory and include at least event streaming, artifact collection, context injection, token usage, cancellation, checkpointing, sandbox visibility, and subagent visibility.

### 5. Artifact evidence

An artifact record has a stable ID, type, path or URI, content digest, producer event, producing run, creation time, media type, and optional size. Patch, diff, test report, build log, screenshot, and final answer artifacts must be linkable from events and eval claims.

Large artifact content remains file-backed. SQLite stores metadata and relationships, not duplicate unbounded payloads.

### 6. Evaluation evidence

An evaluation conclusion must cite events or artifacts. LLM judges may classify or explain ambiguity, but an unsupported judge statement is not sufficient proof of task success, regression, or policy improvement.

P0 failure categories are:

```text
MODEL
CONTEXT
TOOL
ENVIRONMENT
VERIFICATION
POLICY_HARNESS
UNKNOWN
```

Detection and causal attribution are separate fields. The system must be able to abstain.

### 7. Benchmark comparability

Every baseline/candidate experiment must persist an immutable manifest containing:

- task set and task version;
- base agent and adapter version;
- model/provider identity and relevant generation settings;
- tool and sandbox configuration;
- token, time, cost, step, and tool budgets;
- repository or container image revision;
- Praxile policy and asset versions;
- timeout, retry, concurrency, and seed settings when available.

Comparison is invalid when a required invariant differs without being declared as an experiment variable.

### 8. One-variable P0 experiment

The first public experiment changes exactly one Praxile-owned policy. The default candidate is Experience Activation or a narrowly scoped Context Policy. Model, base agent, tools, budget, timeout, task set, and evaluator remain identical.

Trace-only instrumentation must be measured separately so its latency and storage overhead are not misattributed to the candidate policy.

### 9. Clean and Continual tracks

- **Clean Track:** each benchmark task starts with clean project experience state. No test task may teach a later test task.
- **Continual Track:** learning is allowed only across a declared training split; held-out evaluation state is isolated and the result is labeled Continual Harness Evaluation.

Results from the two tracks must never be merged into one headline score.

### 10. Promotion boundary

No benchmark result, semantic judge, Reflect run, or candidate generator can activate a policy automatically. Promotion requires a versioned candidate, baseline comparison, regression decision, evidence links, and explicit human approval. Every promotion records a rollback target.

## P0 deliverables

### P0-A Trace Foundation

- [x] Versioned `AgentEvent`, identity, artifact, capability, and run-handle schemas.
- [x] Schema validation and forward-compatible serialization tests.
- [x] Append-only event storage with idempotent ingestion.
- [x] Run projection and deterministic replay.
- [x] Versioned V1 trajectory import/projection compatibility.
- [x] mini-SWE-agent adapter with explicit capabilities and cancellation.
- [x] One real repository task trace containing context, tool, patch, verification, and result evidence.

### P0-B Eval Runner

- [x] SWE-bench development-subset task loader and repository preparation.
- [x] Official-compatible patch/result handoff and evaluator integration.
- [x] Resumable eval runs and per-task timeout/error isolation.
- [x] Immutable reproducibility manifest.
- [x] Metrics for task success, token, cost, latency, tool calls, retries, recovery, and intervention.
- [x] Baseline report for a fixed model, adapter, task set, and budget: [P0-B public baseline](baselines/P0_B_SWEBENCH_BASELINE.json).

### P0-C Diagnosis and Controlled A/B

- [x] Evidence-first P0 failure taxonomy with abstention.
- [x] Diagnosis records linked to event and artifact IDs.
- [x] Exactly one versioned Context or Experience Activation candidate.
- [x] Baseline and candidate execution under checked invariants: [P0-C public A/B result](baselines/P0_C_BOUNDED_INVESTIGATION_AB.json).
- [x] Per-category gain, regression, cost, and trace-overhead explanation.

### P0-D Public Result

- [x] Reproducible command and environment documentation: [P0 public result](V2_P0_PUBLIC_RESULT.md).
- [x] Machine-readable experiment manifest, raw metrics, and redacted trace samples.
- [x] Baseline/candidate result table with limitations and failed-task breakdown.
- [x] README language that identifies the measured system as Base Agent plus Praxile Control Plane.

## Explicit P0 non-goals

- Building another general coding-agent loop.
- Expanding Praxile's native shell, sandbox, browser, IDE, or terminal backend.
- Claude Code, Codex, DeepSeek Harness, Hermes, or OpenClaw production adapters.
- Implementing a native multi-agent or subagent runtime.
- Web Console redesign, hosted SaaS, multi-tenancy, or PostgreSQL.
- Skill marketplace, automatic model training, or autonomous policy promotion.
- Claiming leaderboard comparability before the clean experiment contract is satisfied.
- Optimizing many policies simultaneously.

Security and correctness fixes to existing V1 features are always allowed and do not count as scope expansion.

## Migration path

1. Add V2 schemas and services beside V1 without changing `praxile run` behavior.
2. Project V1 trajectories into normalized events through a compatibility importer.
3. Add a V2 namespaced CLI for adapter runs, traces, and benchmark evals.
4. Make the mini-SWE reference path runnable before changing public product identity.
5. Move shared evidence, asset, and governance consumers to V2 run projections incrementally.
6. Deprecate V1 runtime ownership only after equivalent audit and governance paths exist for external runs.

## Rollback plan

P0 implementation must remain additive until the reference adapter and trace replay pass their acceptance tests. If the V2 path fails:

- remove or disable the V2 CLI namespace;
- leave V1 commands and `.praxile/` assets readable;
- preserve raw adapter logs and event files for diagnosis;
- avoid irreversible database migrations by using versioned tables and migration backups;
- do not rewrite existing trajectories in place.

## Architecture gate triggers

Reopen this boundary before a change that:

- moves agent-loop or tool-execution policy into V2 Core;
- changes required event semantics or identity cardinality;
- makes core services depend on a specific backend's private schema;
- allows candidate generation to modify sealed evaluation or promotion rules;
- changes Clean Track isolation or benchmark invariants;
- removes human promotion or rollback guarantees;
- treats inferred or LLM-judged claims as objective evidence without provenance;
- expands P0 to multi-agent execution, UI, marketplace, or hosted infrastructure.

## P0 Definition of Done

P0 is complete only when the repository can run a documented command that executes the same public development task set through the same mini-SWE-agent configuration in baseline and Praxile variants, persists replayable normalized traces and evidence-linked artifacts, produces comparable metrics and failure diagnoses, and demonstrates one governed policy candidate with an explicit improve, regress, or inconclusive decision.

Improvement is not required. Reproducibility, attribution, and honest regression reporting are required.
