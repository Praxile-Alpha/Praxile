# Praxile

Praxile is a standalone local self-evolving agent harness for code projects. It is built around its own Environment -> Reward -> Experience loop, with optional adapters for existing tools or agent frameworks:

```text
User Task
  -> Environment Interaction
  -> Trajectory
  -> Reward Report
  -> Experience Proposal
  -> User Approval
  -> Memory / Skill / Eval / Rule Update
  -> Better Next Run
```

Unlike a normal coding agent that only tries to complete the current task, Praxile turns every task into an auditable experience asset. It can propose memories, skills, eval checklists, failure patterns, frozen boundaries, architecture gates, model-routing notes, and harness rules. None of those durable updates are applied until the user accepts a proposal.

The name comes from praxis: useful knowledge formed through practice. The long-term vision is a local project intelligence layer where each bug fix, refactor, UI change, failure, review, and rollback can become a governed asset for future work instead of a forgotten chat transcript.

The product boundary is intentional:

```text
Praxile Agent = provider routing / runtime / environment / reward / experience / evolution / safety
Adapters      = optional bridges to Hermes, OpenClaw, local endpoints, cloud endpoints, or future tool systems
```

Praxile is not a Hermes or OpenClaw plugin. It owns the agent runtime, environment adapters, model routing, reward, trajectory, project memory, project skills, approval, rollback, architecture gates, and harness rules. Hermes/OpenClaw can be detected or adapted later, but they are not required parents.

## Quick Start

From any code repository:

```bash
praxile init
praxile run "Fix the failing parser test" --test-command "python -m pytest"
praxile review
praxile review <PROPOSAL_ID>
praxile accept <PROPOSAL_ID>
praxile history
praxile interop
```

Praxile writes a local `.praxile/` directory in the target project:

```text
.praxile/
  config.json
  memory/
  skills/
  experience/
    trajectories/
    proposals/
      pending/
      accepted/
      rejected/
  evals/
  rules/
    frozen-boundaries/
    architecture-gates/
    harness-rules/
  backups/
  db/
  logs/
```

## Architecture

```text
praxile CLI
  |
  v
Praxile Agent Runtime
  |-- Model Router
  |-- Context Retrieval
  |-- Skill System
  |-- Memory System
  |-- Tool Registry
  |-- Architecture Gate
  |-- Safety Policy
  |
  v
Environment Layer
  |-- FileSystemEnv
  |-- GitEnv
  |-- ShellEnv
  |-- TestEnv
  |
  v
Trajectory Logger
  |
  v
Reward Engine
  |
  v
Evolution Engine
  |
  v
Pending Proposals -> accept/reject/rollback

Gateway
  |-- local HTTP API
  |-- task submission
  |-- review/history/memory endpoints

Optional Adapter Bridge
  |-- Hermes detection
  |-- OpenClaw endpoint/config compatibility
  |-- future external tool/runtime adapters
```

This is intentionally a separate `praxile` package and CLI, not a broad rewrite of another agent framework. The boundary is frozen for the first implementation:

- Praxile owns local code-project `.praxile/` state.
- Praxile owns the Environment -> Reward -> Experience -> Evolution loop.
- Durable self-evolution assets are written only after proposal approval.
- Architecture-sensitive changes stop at an architecture gate before edits.
- external framework integration is optional and mediated through `OptionalAdapterBridge`, a read-only detection boundary in the MVP.

The source directory is also shaped as an agent project surface, not just a module bucket:

```text
praxile/
  *.py
  README.md
  docs/
  templates/
  examples/
```

The templates are runtime assets used by `praxile init` to seed `.praxile/rules/`. The examples show what accepted trajectories and proposals should look like.

For the public install story and optional Hermes/OpenClaw adapter modes, see `docs/INSTALL_AND_INTEROP.md`.

## Agent Boundary

`praxile interop` prints the optional adapter policy, including framework detection status and the adapter matrix. The agent manifest is represented in code so tests can verify the boundary:

- agent id: `praxile.local-self-evolving-agent`;
- kind: `standalone_self_evolving_agent_harness`;
- scope: `local_code_project_self_evolution`;
- state root: `.praxile/`;
- entrypoint: `praxile`.

### Adapter Matrix

| Capability | Optional Adapter | Praxile | Boundary |
| --- | --- | --- | --- |
| provider | Existing endpoint or framework config may inform setup | Task-aware model routing and routing proposals | Praxile can run directly against OpenAI-compatible, local, or cloud endpoints |
| runtime | Framework runtimes may be inspected later through explicit adapters | Task analysis, context retrieval, action loop, trajectory, reward, experience extraction | `praxile run` does not depend on Hermes/OpenClaw chat loops |
| tools / terminal | External terminal/tool systems may become future adapters | Conservative FileSystemEnv, GitEnv, ShellEnv, TestEnv | Execution remains behind Praxile safety policy |
| skills | External native skill stores remain separate | Project-local `.praxile/skills/*/SKILL.md` retrieval and proposals | No automatic install into external framework skills |
| memory | External global memory/profile systems remain separate | Project-local project/decision/failure/repository user memory | No automatic global memory writes |
| gateway | Messaging gateways are optional frontends outside the MVP | CLI-first local project execution | Gateway delivery is not required for self-evolution |
| trajectory | External research/compression tooling may consume exports | Structured audit trajectory plus compatibility sidecar | Sidecar is export; Praxile JSON remains source of truth |
| setup / doctor | External setup/doctor commands remain separate | `praxile init`, `praxile doctor`, `praxile interop` | Praxile validates the standalone harness and optional adapter detection |

## Optional Adapter Boundaries

An external framework may already have persistent memory, user profiles, cross-session recall, skills, and trajectory tooling. Praxile does not silently merge project-local assets into those global systems. The first implementation uses an explicit adapter boundary:

```text
External framework runtime
  - Keeps using native memory, skills, providers, gateway, and chat loop.
  - Does NOT automatically load .praxile/skills.
  - Does NOT automatically receive .praxile/memory as global memory.

Praxile runtime
  - Loads project-local .praxile rules, skills, evals, and memory during praxile run.
  - Writes durable updates only as proposals, then only into the current project's .praxile/.
  - Records a structured local trajectory plus an external-compatible JSONL sidecar.
```

Use `praxile interop` to print the active policy for the current project.

### Skill Loading

`.praxile/skills/*/SKILL.md` files are real inputs for `praxile run`: they are retrieved from the current project and injected into the Praxile runtime context. They are not automatically installed into the external framework's global or bundled skill system.

Load priority inside Praxile is:

1. accepted frozen-boundary, architecture-gate, and harness-rule assets;
2. task-matching `.praxile/skills/*/SKILL.md`;
3. task-matching `.praxile/evals/**/*.md`;
4. project, decision, and failure memory;
5. project-scoped user memory.

This priority is intentionally constraint-first. A frozen boundary or harness rule should shape the run before a procedural skill or memory note does.

### Memory Scope

Praxile memory is project-local by default:

- `.praxile/memory/project.md`: project facts, commands, architecture notes, schemas, and technical context;
- `.praxile/memory/decisions.md`: accepted project decisions and rationale;
- `.praxile/memory/failures.md`: project failure patterns and repair guards;
- `.praxile/memory/user.md`: user preferences observed in this repository only.

External global memory is never updated automatically. A project auth schema, data contract, route layout, or migration rule must stay project-local. A stable cross-project user preference would require a separate explicit global-memory export proposal in a future version.

### Trajectory Compatibility

Praxile does not reuse the external framework's in-memory trajectory structure as its audit source of truth. It writes a structured local trajectory:

```text
.praxile/experience/trajectories/YYYY-MM-DD-task_id.json
```

That JSON includes task, environment snapshot, retrieved experience, plan, actions, observations, diff summary, model route, reward report, and proposals. For research/compression import, Praxile also writes:

```text
.praxile/experience/trajectories/external_compat.jsonl
```

The sidecar uses a ShareGPT-style conversation envelope similar to common trajectory research samples. It is a compatibility export, not the canonical audit record.

Each trajectory also records `loaded_assets`: the memories, skills, rules, evals, or failure patterns that were loaded into the prompt, including `matched_terms`, `matched_fields`, score, and `why_loaded`. The SQLite store mirrors those events in `asset_usage` so `praxile explain <RUN_ID>` can show how accepted experience affected a run. Attribution is deliberately conservative: loaded-only assets are audit context, referenced assets can receive outcome credit, and explicitly used assets receive the strongest positive or negative usage signal.

## Reward Schema

Reward is structured and multi-signal:

```json
{
  "schema_version": 1,
  "task_success": 0.8,
  "execution_score": 0.8,
  "process_safety": 1.0,
  "safety_score": 1.0,
  "regression_passed": true,
  "regression_score": 1.0,
  "scope_control_score": 0.8,
  "cost_score": 0.7,
  "experience_value": 0.6,
  "experience_value_score": 0.6,
  "proposal_quality_score": 0.65,
  "should_generate_experience": true,
  "experience_generation": {
    "should_generate_experience": true,
    "reason": "Reusable evidence was found in diff, tests, or runtime signals.",
    "evidence_strength": "high"
  },
  "requires_human_review": true,
  "objective_signals": {
    "tests_detected": ["python -m pytest"],
    "tests_run": true,
    "tests_passed": true,
    "blocked_actions": 0,
    "architecture_gate_triggered": false
  },
  "llm_assisted_signals": {
    "enabled": false,
    "notes": []
  },
  "manual_signals": {
    "required": true,
    "items": [
      "Review the generated diff for intent and scope.",
      "Confirm the original task is satisfied in the real project.",
      "Accept or reject generated experience proposals explicitly."
    ]
  }
}
```

Objective signals have priority over model judgement. LLM judgement is auxiliary. UI/UX-sensitive tasks and durable evolution updates always keep human review in the loop.

## Proposal Shape

Every proposal carries provenance and scope to reduce experience pollution:

```json
{
  "proposal_id": "prop_123",
  "source_task_id": "task_abc",
  "source_trajectory_id": "task_abc",
  "type": "harness_rule",
  "title": "Require human acceptance for UI-sensitive tasks",
  "reason": "UI tasks cannot be fully verified by click-path tests.",
  "priority": "p1",
  "risk_level": "medium",
  "source": {
    "type": "trajectory",
    "task_id": "task_abc",
    "trajectory_id": "task_abc"
  },
  "evidence": [
    "Task matched UI/interaction keywords.",
    "Reward report marked UI-sensitive work as requiring human confirmation."
  ],
  "evidence_summary": "Task matched UI/interaction keywords. (+1 more signal)",
  "affected_files": ["src/components/Button.tsx"],
  "trigger_reason": "UI-sensitive task requires human acceptance evidence beyond tests.",
  "confidence": 0.66,
  "confidence_level": "medium",
  "future_applicability": "Agent runtime behavior for matching future UI/UX tasks.",
  "applicability_scope": "Agent runtime behavior for matching future UI/UX tasks.",
  "anti_scope": "Do not apply to non-UI tasks or use as permission to bypass safety checks.",
  "generated_by": "deterministic_evolution",
  "target_files": ["rules/harness-rules/ui-human-acceptance.md"],
  "requires_user_approval": true,
  "requires_manual_review": true,
  "status": "pending"
}
```

LLM-assisted evolution is optional and off by default. When enabled, it can only add pending proposals with concrete trajectory evidence, confidence, applicability scope, anti-scope, and safe `.praxile/` target paths. It cannot create architecture gates, frozen boundaries, config mutations, or safety bypasses.

Proposal review is intentionally written in human decision language. The CLI shows what each proposal means, the recommended action (`accept`, `inspect`, `reject_or_edit`, or `inspect_duplicate`), why that recommendation was chosen, affected future retrieval/runtime behavior, duplicate warnings, and the rollback command. `praxile review --recommended <ACTION>` lets a maintainer review by decision bucket instead of raw proposal type.

All indexed experience assets have lifecycle status. Normal retrieval loads only `active` assets; `deprecated`, `superseded`, and `archived` assets remain auditable but stop entering prompts by default. Governance proposals such as `asset_deprecate` and `asset_merge` update sidecar lifecycle metadata, not the original learning note, so accept and rollback can reindex only the affected assets.

Skills use a lifecycle-backed layout:

```text
.praxile/skills/<name>/
  SKILL.md
  metadata.json
  versions/<version>.md
```

Runtime loads only `active` skills. Deprecated or replaced skills remain auditable on disk and in proposal history but are removed from runtime skill retrieval.

Failure patterns include a structured failure type, status, confidence, scope, and anti-scope so repeated safety, regression, model, environment, architecture, UX, or task failures can be searched and converted into future guardrails without turning one bad run into a universal rule.

## Architecture Gate

When a task touches shared contracts, auth/session, routing, storage, migrations, core data flows, or accepted frozen boundaries, Praxile pauses normal implementation before file edits. It records an `architecture_gate` action and proposes an architecture gate asset. Until the gate is reviewed and accepted, the task must not continue as an ordinary feature patch.

This is a hard stop, not a soft note. During a gated run, Praxile blocks `edit_file` and implementation `run_command` actions for the task and returns `needs_human`. The next step is to review or edit the architecture-gate proposal. Implementation must be started as a new explicit task after the gate decision, so the audit trail shows where architecture review ended and coding began.

## UI, Browser, And Human Acceptance

Praxile now includes an optional Playwright browser adapter. When `browser.enabled=true`, the runtime can use `browser_open` and `browser_screenshot` for localhost or explicitly allowed hosts. Screenshot artifacts are written under `.praxile/experience/artifacts/browser/` and recorded in the trajectory.

This is evidence capture, not automatic visual judgement. Praxile can verify page reachability and preserve screenshots, but human review is still required for visual salience, perceived selected state, affordance clarity, and interaction feel.

## Read-Only Concurrency

The runtime supports a `batch` action for read-only tools. It uses `asyncio.gather` and thread offloading to run up to 8 safe read actions concurrently, including `read_file`, `read_files`, `search`, `list_dir`, and `find_files`. `edit_file` and `run_command` are rejected inside batch.

Model actions are parsed and then validated through the Action Schema Registry before execution. A valid JSON object with missing fields, wrong types, unknown action names, invalid `finish.status`, or unsafe batch nesting is rejected with a structured repair prompt instead of being dispatched to tools.

## Model Routing

Praxile includes a local model-router configuration that can use direct provider endpoints or optional adapter-discovered provider settings:

- `planning_model` for architecture framing;
- `coding_model` for code edits;
- `evolution_model` for reward and experience extraction;
- `private_model` for privacy-sensitive work;
- `cheap_model` for low-risk summarization.

Routing choices and failures are recorded in the trajectory and can generate routing proposals. The implementation does not silently mutate provider configuration.

`praxile models --stats` aggregates route performance by task type and target from recorded trajectories. These stats are advisory: they help users decide whether to accept routing proposals, but Praxile still changes routing only through reviewed config edits or accepted harness-rule proposals.

## Hybrid Retrieval

Experience retrieval can run in keyword, vector, or hybrid mode. SQLite/FTS remains the default. When `retrieval.vector_enabled=true`, Praxile stores a local vector row per indexed experience asset. When `retrieval.hybrid_enabled=true`, FTS and vector results are merged and reranked. The built-in `local_hash` embedder is dependency-free; `praxile[vector]` enables `sentence_transformers` for stronger semantic embeddings.

## Semantic Judges

The semantic judge layer is optional and local-first. Heuristics still do candidate recall and enforce safety boundaries; cheap-model judges only refine ambiguous judgement:

- `FeedbackSemanticClassifier` splits complex feedback into run, proposal, asset, or pattern events.
- `AttributionJudge` decides whether a loaded asset truly influenced a run before outcome counters are updated.
- `PatternSemanticJudge` reranks candidate episode pairs when root cause, fix strategy, or verification semantics matter more than exact text overlap.
- `CounterexampleSemanticChecker` checks whether similar episodes or feedback should lower pattern confidence.

Judge output is stored as structured evidence in trajectories, usage rows, patterns, proposals, and explain output. It can change confidence, attribution level, pattern score, counterexamples, and recommended review action, but it cannot bypass proposal approval or write durable assets directly.

## Safety Model

Praxile's safety layer is project-local and conservative:

- path access must remain inside the selected project root;
- sensitive files are blocked by default, including `.env`, `.env.*`, private keys, `.pem`, `.key`, `.p12`, `.pfx`, `.aws`, `.ssh`, secrets, and credential-like filenames;
- writes anywhere under `.praxile/` are blocked through normal agent file actions;
- dangerous command patterns are blocked, including `rm -rf`, `sudo`, `su`, `dd if=`, `mkfs`, recursive chmod/chown, `git reset`, `git clean`, shutdown/reboot, and pipe-to-shell installers;
- compound shell commands, command substitution, most pipes, and unapproved command prefixes are blocked in safe mode; `shell.allow_shell_features=true` can opt into shell execution only with reviewed prefixes for each command segment, project-local redirection targets, and the same dangerous-pattern blocklist;
- allowed commands default to test/lint/build/status operations such as `python -m pytest`, `npm test`, `npm run lint`, `npm run build`, `cargo test`, `go test`, `git status`, and `git diff`;
- file edits are backed up before write, capped by backup retention settings, and can be restored with `praxile rollback <TASK_ID>`;
- accepted proposals store before/after snapshots and can be rolled back with `praxile rollback <PROPOSAL_ID>`;
- state files and JSONL append paths use cross-platform file locks; multi-file proposal accepts keep a recovery journal and restore incomplete commits on the next store initialization;
- configured external-agent lock files and environment flags block normal project writes to prevent concurrent agent edits;
- durable memory, skill, eval, routing, frozen-boundary, architecture-gate, and harness-rule updates require proposal approval.

Safety blocks are recorded in the trajectory and can generate failure-pattern or harness-rule proposals. A safety block should not be worked around by broadening permissions casually; it should become either an explicit config change or a rejected action.
