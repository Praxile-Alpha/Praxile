# Praxile V2 P0-C Diagnosis and Controlled A/B

Status: P0-C accepted; held-out external-agent A/B completed with an inconclusive result
Last updated: 2026-09-11

## Boundary

P0-C diagnoses normalized evidence and compares exactly one Praxile-owned
Context or Experience Activation variable. It does not change the external
agent loop, model, tools, repository, evaluator, task set, or budget.

```text
baseline/candidate AgentEvent + ArtifactRecord
  -> evidence-first FailureDiagnoser
  -> detections (zero or many)
  -> causal attribution (one category or UNKNOWN + abstain)
  -> clean-track ContextCandidate
  -> immutable paired experiment manifest
  -> identical P0-B runs
  -> invariant check
  -> category, outcome, efficiency, and trace-overhead comparison
  -> improve | regress | inconclusive
```

No result promotes the candidate automatically. Promotion remains a separate
human-governed operation with a rollback target.

## Failure Taxonomy

The frozen categories are `MODEL`, `CONTEXT`, `TOOL`, `ENVIRONMENT`,
`VERIFICATION`, `POLICY_HARNESS`, and `UNKNOWN`.

Detection and attribution are separate:

- a detection records a directly observed condition and cites `event:<ID>` or
  `artifact:<ID>`;
- attribution is emitted only when an objectively failed task has exactly one
  directly supported category;
- multiple categories, absent evidence, unknown evaluator output, and resolved
  tasks produce `UNKNOWN` with `abstained=true`;
- model or LLM explanations are not accepted as objective task-success proof.

The P0 classifier deliberately favors abstention over speculative causality.

```bash
praxile eval diagnose <BENCHMARK_RUN_ID>
```

Diagnosis sidecars are written under
`.praxile/eval/v2/runs/<RUN_ID>/tasks/<TASK_ID>/diagnosis.json`.

## Candidate

P0 accepts exactly one `praxile.context_candidate.v1` object. It contains one
`context_item`, source task and diagnosis IDs, event/artifact evidence,
confidence, scope, anti-scope, and expected effects. It cannot change budgets or
adapter settings when converted into an `AdapterPolicy`.

The first candidate is
[bounded investigation](candidates/P0_C_BOUNDED_INVESTIGATION.json). It was
derived from a resolved baseline whose native agent still required 128 model
and tool calls and ended with an internal provider error. The candidate is not
allowed to evaluate on its source task.

## Controlled A/B

```bash
praxile eval ab docs/candidates/P0_C_BOUNDED_INVESTIGATION.json \
  --experiment-id p0-c-bounded-investigation-001 \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --instance-id sympy__sympy-18532 \
  --model dashscope/qwen3-coder-next \
  --cost-tracking ignore_errors \
  --timeout 900 \
  --step-limit 50
```

Use `--resume` after interruption. The pair manifest freezes task-set digest,
adapter, model, evaluator, execution configuration, budgets, settings, and the
single changed path `policy.context[0]`. Both run manifests are checked again
after execution. A mismatch invalidates the experiment.

Recompute diagnoses and the comparison from persisted evidence without calling
the model or evaluator again:

```bash
praxile eval ab-analyze p0-c-bounded-investigation-001
```

The report includes task transitions, gain/regression counts grouped by the
baseline failure category, token/tool/latency deltas, monetary cost only when
both arms use valid cost tracking, and serialized trace deltas. Trace
instrumentation is identical in both arms; storage deltas are reported but are
not claimed to measure tracer latency causality.

## Accepted P0-C Run

The checked-in [P0-C controlled A/B result](baselines/P0_C_BOUNDED_INVESTIGATION_AB.json)
uses held-out task `sympy__sympy-18532`; the candidate source task is
`sympy__sympy-20590`. The invariant check passed and confirmed that only
`policy.context[0]` changed.

Both arms exhausted the same 50-step budget without producing a patch. The
evidence-first diagnoser therefore classified both failures as
`POLICY_HARNESS / execution_budget_exhausted`. The candidate consumed 34,962
fewer total tokens and 18,948 fewer milliseconds but did not reduce tool calls
or establish objective task success. The decision is consequently
`inconclusive`, and the candidate is not promoted.

## Decision Rule

- `regress`: any objectively resolved baseline task becomes unresolved;
- `improve`: at least one unresolved task becomes resolved with no regression,
  or outcomes are identical and token/tool usage improves monotonically;
- `inconclusive`: evaluator evidence is missing, or outcomes and efficiency do
  not establish a monotonic improvement.

Improvement is not required for P0-C acceptance. Honest attribution and a
reproducible negative or inconclusive result are valid outcomes.
