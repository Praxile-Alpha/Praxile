# Praxile V2 P1 Frozen Boundary

Status: frozen  
Effective date: 2026-09-13  
Decision type: architecture boundary

## Goal

P1 turns Context Policy, Skill, Subagent Control, and Harness Evolution into governed control-plane contracts. Praxile selects, evaluates, and promotes policies; the connected agent runtime still performs execution.

## Frozen ownership

- Praxile owns versioned policies, evidence-linked assets, delegation contracts, evaluation gates, promotion state, and rollback pointers.
- The external adapter owns model calls, tools, process lifecycle, and native subagent execution.
- V1 runtime, `.praxile/skills`, and existing project experience remain backward compatible. They are not silently promoted into V2 assets.
- An adapter receives policy only through `AdapterPolicy`; it must not infer an active policy from files on disk.
- A Candidate may run only in an explicitly marked evaluation. Production injection requires `active` status.
- No judge, benchmark, Reflect run, or candidate generator may promote itself.

## Evidence boundary

Every governed asset and every promotion gate cites typed evidence: normalized event, artifact, eval, diagnosis, run, or user feedback. Free-form prose may explain evidence but cannot replace it.

## P1 contracts

### Context Policy

A policy declares context sources, stage applicability, source budgets, retrieval/history/repository/experience settings, and separate exploration, implementation, and verification budgets. Candidate compilation is evaluation-only; active compilation is eligible for production adapter runs.

Context sources are task/spec, repository map, current state/checkpoint, recent trajectory, retrieved experience, retrieved skill, tool result, subagent result, and artifact/diff/test evidence.

Experience candidates with `applies_to.repositories`, `task_signals`, or
`does_not_apply_when` pass a deterministic semantic activation gate before
execution. The gate requires repository scope plus a configured task-signal
coverage threshold, gives anti-scope precedence, and abstains when evidence is
missing. The immutable experiment manifest stores the complete activation plan;
each run emits `CONTEXT_ACTIVATION`, while only activated tasks emit
`CONTEXT_INJECT`. Activation uses task text and metadata but persists only
matched terms, scores, thresholds, and reasons.

### Skill Asset

A V2 Skill is not a `SKILL.md` prompt. It contains typed inputs, preconditions, context requirements, allowed tools, procedure, verification contract, known failure modes, scope, version, and eval cases. A human-readable Markdown projection may be added later, but the versioned JSON contract is authoritative.

### Subagent Control

Praxile does not create a native subagent runtime. It defines the delegation objective, scope, artifacts, context topology, tools, backend/model, token/time/cost budgets, termination criteria, verification criteria, and return schema. Parent and child executions remain a trace DAG. Merge requires evidence from an isolated verifier run.

### Harness Evolution

Candidate types are Prompt, Context Policy, Retrieval, Skill, Tool Policy, Model Routing, Subagent Policy, and Recovery Policy. Promotion requires exactly six passing gates:

1. Evidence
2. Quality
3. Regression
4. Cost
5. Human
6. Rollback

Quality includes a mandatory diff-scope/minimality sub-gate for benchmark
evidence. Every candidate task result records changed files and lines, source and
test component scopes, threshold policy, and explicit review reasons. Missing
scope evidence or a candidate marked `review_required` fails Quality even when
resolution, token, or latency metrics improve. This does not create a seventh
gate or silently modify the A/B outcome; it makes the existing Quality gate
explainable and resistant to broad incidental patches.

The local registry stores candidate, evaluation, active pointer, and history in one atomically replaced state file. Rollback restores the previous active pointer and never deletes evidence.

## Migration and rollback

This phase is additive. It introduces `praxile.control_plane` and `.praxile/control-plane/registry.json`; it does not change the existing database schema or native CLI execution. Rolling back the phase means stopping use of the new package and registry. Existing V1 state remains readable.

## P1 Checklist

### P1-A Context Policy

- [x] Versioned context source and stage-budget schemas.
- [x] Candidate versus active compilation boundary.
- [x] Explicit `AdapterPolicy` bridge and policy-use metadata.
- [x] Repository-, semantic-signal-, and anti-scope-aware candidate activation
  with auditable abstention.
- [ ] Persist per-run source utilization and compression decisions in trace events.
- [x] Run two complete Context Policies under frozen task/model/adapter/evaluator invariants (fixture acceptance).
- [x] Publish a model-backed Context/Experience ablation on a fixed five-task
  held-out set ([MiniMax M3 held-out result](baselines/P1_MINIMAX_M3_HELDOUT5_STOPPING_V1/README.md));
  the result is inconclusive and explicitly not promoted.

### P1-B Skill Asset

- [x] First-class versioned Skill Asset schema.
- [x] Preconditions, tools, procedure, verification, failure modes, and eval cases are mandatory.
- [x] Proposed skills cannot be injected into production runs.
- [x] Skill candidate evaluator and deterministic human-readable Markdown projection.
- [ ] Measured Skill activation and attribution experiment.

### P1-C Subagent Control

- [x] Versioned Delegation Contract and context topology modes.
- [x] Parent-child trace DAG validator.
- [x] Isolated verifier Merge Decision gate.
- [x] Capability-negotiated delegation service for a subagent-capable adapter.
- [ ] Controlled no-subagent/subagent or fresh/fork comparison.

### P1-D Harness Evolution

- [x] Versioned Candidate and six-gate Evaluation schemas.
- [x] Atomic local candidate/promotion/rollback registry.
- [x] Explicit human approval and one-command-equivalent registry rollback operation.
- [x] Eval Runner integration that produces Evidence, Quality, Regression, Cost, Human, and Rollback gates.
- [x] CLI commands for candidate register/list/evaluate/promote/rollback and Skill evaluation.
- [ ] End-to-end candidate promotion and rollback evidence package.

## CLI Entry Points

```bash
# Compare two complete Context Policies under the same benchmark invariants.
praxile eval context-ablation policy-a.json policy-b.json tasks.json \
  --context-a context-a.json --context-b context-b.json \
  --experiment-id context-policy-v1-v2 --model MODEL

# Evaluate a Skill Asset and render its deterministic review document.
praxile harness skill-evaluate skill.json --results skill-results.json \
  --markdown-output .praxile/reviews/skill.md

# Register, gate, promote, inspect, and roll back one Harness Candidate.
praxile harness candidate-register candidate.json
praxile harness candidate-evaluate CANDIDATE_ID --ab-report report.json \
  --reviewer MAINTAINER --approve-human
praxile harness candidate-promote CANDIDATE_ID --approved-by MAINTAINER
praxile harness candidate-list
praxile harness candidate-rollback COMPONENT_KEY --approved-by MAINTAINER
```

`candidate-evaluate` does not imply promotion. Without `--approve-human`, the Human Gate remains false and the decision abstains. A later `candidate-promote` command succeeds only when all six persisted gates passed.
