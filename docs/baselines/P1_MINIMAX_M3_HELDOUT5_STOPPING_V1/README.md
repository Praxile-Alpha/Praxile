# MiniMax M3 Held-Out Context Experiment

Status: completed, not eligible for promotion

Experiment: `p1-bounded-investigation-minimax-m3-heldout5-stopping-v1`

Recorded: 2026-09-14

## Scope

This clean-track A/B experiment evaluates the bounded-investigation Context
Candidate on five same-repository held-out SWE-bench Lite tasks. The source task
(`sympy__sympy-20590`) and the earlier evaluation task
(`sympy__sympy-18532`) are excluded. It measures within-project transfer, not
cross-repository generalization.

Both arms use mini-SWE-agent 2.4.6, MiniMax M3, the official SWE-bench 5.0.2
evaluator, a 150-step ceiling, the same runtime stopping policy, and the same
diff-scope policy. The only experiment variable is `policy.context[0]`.

## Result

The invariant check passed, but the comparison is `inconclusive`:

| Metric | Baseline | Candidate | Delta |
| --- | ---: | ---: | ---: |
| Resolved | 0/5 | 0/5 | 0 |
| Tool calls | 227 | 229 | +2 |
| Input tokens | 4,391,708 | 5,638,246 | +1,246,538 |
| Output tokens | 85,552 | 87,111 | +1,559 |
| Wall latency | 1,580,778 ms | 1,760,927 ms | +180,149 ms |

Two tasks used fewer calls under the candidate, but three did not improve
monotonically. `sympy__sympy-16281` changed 2,563 lines and was marked
`review_required` by the diff-scope/minimality gate. Therefore this candidate
must not be promoted.

All ten runs emitted a hard `STOP_DECISION`: seven stopped after successful
local verification and three exhausted the bounded post-patch budget. Local
verification is process evidence, not benchmark success; the official evaluator
resolved none of the patches.

## Evidence

- `experiment-manifest.json`: redacted frozen inputs and both arm manifests.
- `raw-metrics.json`: per-task objective results, diagnoses, scope evidence, and
  aggregate comparison.
- `trace-sample.jsonl`: redacted run boundaries, context activation,
  verification, stopping decisions, artifacts, and final results.

Cost is not comparable because the provider run used `ignore_errors` for a model
without local pricing metadata. No human approval was recorded.

## Follow-up Activation Gate

This experiment intentionally predates semantic activation gating and injected
the candidate into all five tasks. It exposed that repository match alone is too
broad. Replaying the frozen task text through the subsequently implemented
`praxile.context_activation.v1` gate produced 0 activations and 5 abstentions
(`decision_digest=sha256:ff385a4cffa0e8759bc1584a063c4d0a4be2eb2ff45897271da3c6b0653b421b`).
None of the tasks reached the candidate's configured signals for shared
inheritance, object layout, or regression across versions. This replay did not
call a model and does not alter the historical experiment manifest or metrics.
