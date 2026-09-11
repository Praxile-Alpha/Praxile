# Praxile V2 Current State Gap Audit

Status: completed for the V2 P0 boundary freeze  
Audit date: 2026-09-11  
Scope: repository state before V2 implementation

> This document records the pre-implementation audit. Current P0-A implementation status is tracked in [V2 Trace Foundation](V2_TRACE_FOUNDATION.md), [V2 Agent Adapters](V2_AGENT_ADAPTERS.md), and the frozen checklist.

## Executive conclusion

Praxile V1 already contains useful governance machinery, but it is not yet the V2 control plane described in the design handoff. The current system is centered on Praxile's own coding runtime and a task-level trajectory document. V2 requires an external execution plane, a stable event stream, backend adapters that can run and observe agents, benchmark-grade evaluation, and failure attribution over trace evidence.

The migration should preserve the Experience Store, evidence model, proposal governance, isolated candidate validation, semantic attribution, and rollback mechanisms. It should not continue expanding the V1 agent loop, shell, browser, gateway, or provider surface as V2 core capabilities.

## Audit method

The audit mapped every V2 P0 deliverable to implementation, tests, CLI surface, persistence, and documentation. Status meanings are:

- **Reusable**: implemented with a contract that can survive into V2 with limited adaptation.
- **Partial**: useful code exists, but its ownership or data contract does not meet V2.
- **Missing**: no runnable implementation satisfying the V2 requirement exists.
- **Legacy**: functional V1 code that remains supported temporarily but is not part of V2 core growth.

## Capability gap matrix

| V2 capability | Current implementation | Status | Required P0 action |
|---|---|---:|---|
| Stable `AgentEvent` schema | `TrajectoryLogger` stores task-level lists of actions, observations, artifacts, routing, reward, and proposals. It has no stable event ID, trace/run hierarchy, tool-call correlation, or event-level evidence contract. | Missing | Add a versioned event schema and validation before writing new adapters. |
| Trace, run, task, step, tool, and artifact identity | V1 mainly uses `task_id`; executor relationships exist, but `trace_id`, `run_id`, `parent_run_id`, `step_id`, `tool_call_id`, and stable artifact identity are not first-class. | Missing | Introduce explicit identifiers and define cardinality and parent-child rules. |
| Append-only trace collection | Trajectories are written as final JSON documents and indexed into SQLite. Trace JSONL currently means diagnostic logs or a ShareGPT-compatible export, not the canonical event stream. | Missing | Add append-only event persistence with idempotent ingestion and final run projection. |
| Trace replay | A stored trajectory can be inspected, but no service reconstructs run state from ordered normalized events. | Missing | Build deterministic event replay and consistency checks. |
| V2 `AgentAdapter` contract | `AgentAdapter` only converts external output to a Praxile trajectory and exports a proposal payload. | Partial | Replace or version the interface around `run`, `stream_events`, `get_artifacts`, `cancel`, and `capabilities`. |
| Generic external import | `GenericJSONLAdapter` imports generic rows and produces a V1 trajectory. | Partial | Retain it as an ingestion compatibility adapter and emit normalized events instead of inventing reward defaults. |
| mini-SWE-agent execution adapter | No adapter starts or observes mini-SWE-agent. | Missing | Implement the first reference execution adapter without modifying mini-SWE-agent's agent loop. |
| Adapter capability negotiation | Optional Hermes detection reports installed modules, but it does not express execution, streaming, context injection, checkpoint, sandbox, token, or subagent capabilities. | Missing | Add a typed capability manifest and explicit degraded behavior. |
| Artifact store | V1 trajectories can contain artifacts and diff summaries; screenshots and reports use project-local paths. There is no common artifact record with stable ID, digest, media type, producer event, and provenance. | Partial | Add an artifact schema and immutable metadata records while keeping large payloads file-backed. |
| Evidence-linked evaluation | Reward claims and evidence refs are indexed; proposal validation protects expected outputs and sealed sets. | Reusable | Adapt evidence refs to event and artifact IDs rather than only trajectory fields and paths. |
| General Eval Suite | `EvalSuite` and `EvalRunner` evaluate proposal generation and reviewed shell-command cases. | Partial | Keep this path for control-plane unit evals; add a separate agent-system benchmark runner. |
| SWE-bench runner and evaluator | No task loader, repository preparation, patch submission, Docker evaluator, or benchmark result importer exists. | Missing | Add a development subset runner first, with optional SWE-bench dependencies and resumable results. |
| Benchmark reproducibility manifest | Runtime records model route and costs, but no experiment manifest freezes model, base agent, tools, budget, timeout, task set, image, seed, and policy version. | Missing | Make the manifest mandatory for every P0 benchmark eval. |
| Baseline versus Praxile A/B | `ProposalValidationLab` compares a harness component in two copied project states. `--without-experience` provides a local retrieval control. Neither is a benchmark-level Base Agent A/B runner. | Partial | Reuse comparison logic, but execute identical benchmark tasks through the same external adapter under two policy variants. |
| Core benchmark metrics | Reward contains success, safety, regression, cost, and review signals. Eval metrics primarily score generated proposal shape. | Partial | Standardize resolved/pass, token, cost, latency, tool calls, retries, recovery, and intervention from events. |
| Failure taxonomy | Episodes and silent-failure detectors identify specific patterns such as test repair and blocked actions. They do not consistently classify MODEL, CONTEXT, TOOL, ENVIRONMENT, VERIFICATION, and POLICY/HARNESS. | Partial | Add a small evidence-first taxonomy with `unknown` and abstention support. |
| Failure attribution | Reward evidence and semantic judges link claims to trajectory data, but attribution is oriented toward experience assets rather than all execution failures. | Partial | Produce diagnosis records whose claims cite event and artifact IDs and distinguish detection from causal attribution. |
| Context source observability | Repository sync, retrieval explanations, ContextJuice profiles, compression evidence, and loaded-asset activation are implemented. | Reusable | Emit every context injection, source, digest, token estimate, policy version, and target run as events. |
| Minimal Context or Experience Candidate | Retrieval policies, harness components, candidate validation, activation attribution, and rollback exist. | Reusable | Select exactly one P0 candidate, preferably experience activation, and express it as a versioned V2 policy. |
| Candidate governance | Component-scoped proposals require base/candidate versions, validation, human acceptance, monitoring, and rollback. | Reusable | Preserve this model and bind promotion evidence to benchmark eval runs. |
| Public benchmark report | Existing audit and eval reports are project-local and not a SWE-bench result table with raw reproducibility metadata. | Missing | Generate a machine-readable report plus a concise baseline/candidate table and failure breakdown. |
| Web Console | A React/Vite console and Gateway exist. | Legacy for P0 | Keep working, but do not let UI work block or shape P0 contracts. |
| Praxile native coding runtime | `AgentRuntime`, providers, tools, shell, browser, checkpointing, and safety are substantial V1 features. | Legacy for V2 | Maintain compatibility and use only as an optional reference integration. Do not expand it to satisfy V2 execution requirements. |

## Assets to preserve

The following V1 subsystems should be migrated, not rewritten wholesale:

1. `ExperienceStore` file and SQLite model for searchable, auditable project state.
2. Reward evidence and semantic attribution, after moving their references to normalized events and artifacts.
3. Governed assets, proposal inbox, scope, confidence, anti-scope, lifecycle, and review history.
4. `HarnessComponentRegistry`, baseline/candidate validation concepts, sealed eval ownership, and stale-base checks.
5. Human acceptance, snapshots, atomic proposal application, monitoring, and rollback.
6. Context retrieval explanations, activation funnel, and ContextJuice profile metadata.
7. Reflect and failure-pattern mining as consumers of normalized run projections.

## Components to contain

The following code remains supported during migration but must not define V2 core contracts:

- `praxile.runtime.AgentRuntime`
- `FileSystemEnv`, `GitEnv`, `ShellEnv`, `TestEnv`, and `BrowserEnv`
- direct model-provider execution for the V1 agent loop
- Gateway channel execution and Web Console orchestration
- Hermes/OpenClaw module detection as a substitute for a real adapter
- ShareGPT sidecar export as a substitute for normalized trace storage

Bug fixes and security fixes remain allowed. New execution features require reopening the V2 boundary review.

## Architectural debt exposed by the audit

1. Public positioning and `identity.py` still describe Praxile as a standalone self-evolving agent harness that owns the runtime. This must migrate only when the V2 reference path is runnable; until then, documentation must distinguish current V1 capability from the frozen V2 direction.
2. `AgentAdapter` is currently a format converter, not an execution boundary. Changing it without versioning would break existing imports.
3. A trajectory is both audit record and internal runtime state. V2 needs canonical events with trajectories as projections, otherwise adapters will keep encoding backend-specific assumptions into one large object.
4. Current evals mix control-plane proposal tests with command execution. Benchmark evaluation needs separate ownership, dependencies, isolation, and result schemas.
5. SQLite has rich experience indexing but no event/run/artifact tables for the V2 identity model.
6. P0 cannot claim fair A/B until experiment invariants are persisted and checked, not merely documented.

## Facts and assumptions

### Confirmed facts

- V1 can run independently and should remain usable during migration.
- Existing experience governance is the strongest reusable part of the system.
- No mini-SWE-agent or SWE-bench execution integration currently exists.
- Current external interop is import and capability detection, not execution control.
- Current project-copy validation is not an operating-system or benchmark Docker sandbox.

### Working assumptions

- mini-SWE-agent remains the first P0 base agent because its loop is small and experimentally controllable.
- The exact mini-SWE integration transport is not frozen until its supported CLI and library interfaces are verified.
- P0 uses SQLite and file-backed artifacts; PostgreSQL is not required.
- A local development subset is acceptable before a clean SWE-bench Verified run.
- V1 CLI commands remain available while V2 commands are introduced under explicit namespaces.

### Conditions that reopen the audit

- The chosen base agent cannot expose tool, patch, result, or timing events with sufficient fidelity.
- SWE-bench licensing or execution constraints prevent reproducible local integration.
- Event volume makes the proposed SQLite and JSONL design unusable at the development-subset scale.
- Supporting V1 and V2 in one package creates ambiguous state ownership or unsafe migration behavior.

## Recommended implementation order

1. Freeze the contracts in `V2_P0_FROZEN_BOUNDARY.md`.
2. Add schema-only packages for trace identity, events, artifacts, adapters, and experiment manifests.
3. Add validation and round-trip tests before persistence or external process execution.
4. Add append-only event persistence and replay.
5. Implement mini-SWE-agent capability detection and a dry-run fixture adapter.
6. Run one local task end to end and verify event/artifact evidence.
7. Add the SWE-bench development runner and baseline report.
8. Add one Context/Experience policy candidate and run the controlled A/B.
