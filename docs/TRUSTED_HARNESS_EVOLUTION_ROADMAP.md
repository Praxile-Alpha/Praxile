# Trusted Harness Evolution Roadmap

> Status: active implementation roadmap. This document distinguishes implemented foundations from planned work. Checking an item requires code, tests, documentation, and an inspectable artifact or CLI/Web surface.

## Goal

Move Praxile from governed experience storage to governed, measurable harness evolution:

```text
Trajectory
  -> Evidence and Failure Diagnosis
  -> Minimal Component Proposal
  -> Shadow Evaluation
  -> Regression and Held-out Validation
  -> Human Decision
  -> Versioned Activation
  -> Future-run Attribution
```

## Frozen Boundaries

These constraints are not ordinary evolution targets:

- durable experience remains human-approved by default;
- project assets cannot silently become global memory;
- LLM judges remain advisory and cannot overwrite objective evidence;
- sensitive path, command, secret, and architecture gates cannot be weakened by normal proposals;
- every applied update has provenance, version, rollback data, and an audit event;
- sealed eval ownership stays outside the proposal composer and task agent;
- improvement claims require comparison evidence, not only a successful source run.

Changing one of these constraints requires an explicit architecture proposal and human approval.

## Implemented Foundation

- [x] Repository-local trajectory ledger and structured reward report
- [x] Evidence, episode, pattern, and proposal pipeline
- [x] Human review, edit, accept, reject, lifecycle state, and rollback
- [x] Project-local memory, skills, rules, evals, patterns, and boundaries
- [x] Retrieval explanation and asset-usage attribution records
- [x] Experience graph and offline Reflect governance
- [x] Objective, human-feedback, and optional LLM-assisted reward lanes
- [x] Architecture gate and frozen-boundary assets
- [x] Model roles and provider routing foundation

## P0: Make Experience Activation Observable

The system must prove that an accepted asset participated in later behavior.

- [x] Add a persisted activation state machine: `eligible`, `retrieved`, `injected`, `referenced`, `complied_with`, `outcome_attributed`
- [x] Record activation events with run, asset version, model role, executor, timestamp, and evidence pointers
- [x] Separate positive, negative, neutral, and unknown attribution; never infer positive contribution from run success alone
- [x] Expose activation funnels in `praxile explain`, graph queries, audit exports, and the Web Console
- [x] Add retrieval-control runs where accepted project experience is deliberately withheld for comparison (`praxile run --without-experience`)
- [x] Add metrics: activation rate, compliance rate, positive contribution rate, harmful rate, and attribution coverage

Acceptance criteria:

- A user can answer why an asset was eligible, whether it entered the prompt, whether the agent followed it, and what evidence supports its outcome attribution.
- Existing `loaded_only` records migrate without being misclassified as behavioral compliance.

Implementation note: the compatibility migration maps legacy usage only to `eligible`, `retrieved`, `injected`, and explicit `referenced` events. It never manufactures `complied_with` or `outcome_attributed` evidence.

## P1: Proposal Validation Lab

Turn accepted harness changes into versioned experiments.

- [x] Define a Harness Component Registry for prompts, retrieval policy, skills, rules, model routing, tool policy, compression profile, stopping policy, and eval policy
- [x] Require every harness proposal to name exactly which component and version it changes
- [x] Add shadow evaluation that applies candidate changes in isolated baseline/candidate copy workspaces
- [x] Support source, regression, and sealed/held-out eval sets with independent ownership metadata
- [x] Compare baseline and candidate on task success, safety, regressions, cost, latency, and human-review burden
- [x] Introduce proposal states: `proposed`, `shadow_running`, `validated`, `inconclusive`, `regressed`, `accepted`, `rolled_back`
- [x] Prevent a proposal composer from reading sealed expected outputs or modifying its scorer

Acceptance criteria:

- Praxile can produce a baseline-versus-candidate validation report and refuse to label an update as an improvement when evidence is inconclusive.
- Failure during multi-case validation leaves the active harness unchanged.

Implementation note: `praxile proposal validate <PROPOSAL_ID> --suite <SUITE.json>` executes candidate inputs without expected outputs, then scores observations in the parent validation process. Candidate writes are confined to the isolated `.praxile/` copy, and `accept` still requires a separate human command after validation.

## P2: Reward Evidence Graph and Judge Calibration

Replace opaque aggregate scores with an explainable evidence model while preserving a compact summary score.

- [x] Version reward profiles by repository and task class
- [x] Represent reward claims as nodes linked to tests, commands, diffs, screenshots, specs, user decisions, and judge outputs
- [x] Track signal provenance as `objective`, `derived`, `llm_assisted`, or `human_confirmed`
- [x] Add judge calibration suites with controlled trajectory mutations and known expected detections
- [x] Store judge confusion metrics, abstention rate, disagreement, model/version, prompt version, and evidence coverage
- [x] Require escalation when judges disagree, evidence is missing, or architecture/UX risk exceeds policy
- [x] Generate reward-policy proposals from repeated miscalibration, but route them through the architecture gate

Operational interfaces:

- `praxile reward explain <RUN_ID>` displays the frozen reward profile and evidence-backed claims.
- `praxile judge calibrate <SUITE.json>` runs controlled mutations without calling a model.
- `praxile judge calibrate <SUITE.json> --write-proposal` may create a high-risk, architecture-gated `reward_policy` proposal only after repeated calibration misses. It never applies the policy automatically.

Acceptance criteria:

- Every material reward claim is traceable to evidence.
- Changing a weight or judge cannot silently reinterpret historical reports; schema and profile versions remain visible.

## P3: Bounded Harness Evolution

Allow Praxile to improve selected harness components without granting unrestricted self-modification.

- [x] Mine repeated failure pathologies across episodes rather than optimizing for isolated tasks
- [x] Generate minimal, component-scoped alternatives with explicit applicability and anti-scope
- [x] Maintain a quality-diversity archive keyed by failure pathology and affected component
- [x] Use component-wise experiments before consolidating multiple updates
- [x] Add routing proposals based on task class, privacy, risk, measured model performance, cost, and judge reliability
- [x] Add automatic rollback triggers for statistically or operationally significant regressions
- [x] Require human promotion from `validated` candidate to active project harness
- [x] Support exportable experiment bundles without exporting private repository content by default

Operational workflow:

```bash
praxile harness mine
praxile harness propose <PATHOLOGY_ID>
praxile proposal validate <PROPOSAL_ID> --suite <SUITE.json>
praxile accept <PROPOSAL_ID>              # explicit human promotion
praxile harness manifest
praxile harness export <PROPOSAL_ID> --output experiment.zip
```

`harness export` emits hashes, component versions, and validation summaries by default. Repository proposal content is included only when both project policy and the explicit CLI flag allow it. Automatic rollback requires evidence that the promoted component participated in the regressing run; unrelated failures do not roll components back.

Acceptance criteria:

- An active harness version has a manifest of component versions, validation evidence, approval, activation date, and rollback target.
- No normal evolution action can modify the frozen outer anchor.

## Measurement Dashboard

The roadmap should be judged with operational metrics, not proposal volume:

| Dimension | Example metrics |
|---|---|
| Task outcome | success, regression pass rate, spec compliance |
| Safety | blocked unsafe actions, policy violations, human escalations |
| Experience quality | proposal acceptance, later deprecation, contradiction, harmful rate |
| Activation | retrieval, injection, reference, compliance, attribution coverage |
| Evolution | baseline delta, held-out delta, confidence interval, rollback rate |
| Efficiency | tokens, model calls, tool calls, latency, human review time |
| Generalization | cross-task, cross-repository profile, and cross-model transfer |

Proposal count is not a success metric. A smaller set of validated, frequently activated assets is preferable to a large passive memory store.

## Research-to-Module Map

| Research lesson | Praxile module surface |
|---|---|
| Weakness mining before proposing | `episodes.py`, `patterns.py`, `evolution.py`, Reflect |
| Proposal and credit separation | `evolution.py`, `reward.py`, eval runner, proposal service |
| Dual-layer case/global experience | evidence, episodes, project patterns, retrieval |
| Activation versus benefit | store feedback/usage, trajectory, graph, explain UI |
| Component-wise harness evolution | model roles, skills, rules, retrieval, context profiles |
| Sealed validation | eval suites, isolated workspace, audit policy |
| Frozen outer anchor | constitution, safety policy, architecture gates, governance service |

## Non-Goals

- automatic model-weight training in the near-term product;
- unrestricted recursive self-modification;
- optimizing only benchmark scores while ignoring safety and cost;
- treating more memory, more skills, or more proposals as evidence of improvement;
- claiming causal attribution from one uncontrolled task run;
- silently synchronizing project experience across repositories or users.

## Source Context

The research and product evidence behind this plan is summarized in [Agent Harness Landscape 2026](AGENT_HARNESS_LANDSCAPE_2026.md). Preprint findings should be treated as directional until independently reproduced in Praxile's own evaluation environment.
