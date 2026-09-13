# Praxile V2 P0 Public Result

Status: P0 public reference path accepted
Recorded run: 2026-09-11
Published evidence: 2026-09-13

## Measured System

The measured system is explicitly:

```text
mini-SWE-agent 2.4.6 (Base Agent / execution plane)
  + Praxile 0.1.0 (trace, context candidate, diagnosis, A/B control plane)
  + SWE-bench 5.0.2 (objective evaluator, when a patch exists)
```

mini-SWE-agent owns planning, model calls, tool use, and code execution. Praxile
does not claim those capabilities or report the result as a standalone Praxile
coding-agent score. Praxile owns the normalized evidence, one Context/Experience
candidate, frozen experiment invariants, failure diagnosis, comparison, public
redaction, and governance decision.

## Result

The clean-track experiment used one held-out SWE-bench Lite task. The candidate
was derived from `sympy__sympy-20590` and evaluated on
`sympy__sympy-18532`, so no evaluation task taught a later evaluation task.

| Metric | Base Agent | Base Agent + Praxile candidate | Delta |
|---|---:|---:|---:|
| Objective resolution | unknown | unknown | inconclusive |
| Input tokens | 695,638 | 663,706 | -31,932 |
| Output tokens | 6,544 | 3,514 | -3,030 |
| Total tokens | 702,182 | 667,220 | -34,962 |
| Tool calls | 50 | 50 | 0 |
| Wall latency | 78,553 ms | 59,605 ms | -18,948 ms |
| Failure category | `POLICY_HARNESS` | `POLICY_HARNESS` | unchanged |

Both arms reached the same 50-step limit without producing a patch. The
official evaluator therefore had no prediction to evaluate. Native
`LimitsExceeded` final events support the diagnosis
`POLICY_HARNESS / execution_budget_exhausted` in both arms.

The candidate reduced token use and latency in this run, but did not reduce
tool calls or establish task success. The controlled decision is therefore
`inconclusive`, not `improve`, and the candidate is not promoted.

## Reproduce

Prerequisites:

- Python 3.11 or newer;
- Docker with enough resources for SWE-bench images;
- a model endpoint supported by mini-SWE-agent;
- the provider credential in an environment variable, never in repository
  configuration or command history.

Install from source:

```bash
git clone https://github.com/Praxile-Alpha/Praxile.git
cd Praxile
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[benchmark]"
docker version
```

The accepted run used `dashscope/qwen3-coder-next`. Configure its credential in
the environment, then run the single-variable experiment:

```bash
export DASHSCOPE_API_KEY="<your-key>"

praxile eval ab docs/candidates/P0_C_BOUNDED_INVESTIGATION.json \
  --experiment-id p0-c-bounded-investigation-001 \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --instance-id sympy__sympy-18532 \
  --model dashscope/qwen3-coder-next \
  --cost-tracking ignore_errors \
  --timeout 900 \
  --step-limit 50
```

Use `--resume` after an interruption. Reanalysis and public export never call
the model or evaluator again:

```bash
praxile eval ab-analyze p0-c-bounded-investigation-001
praxile eval export-public p0-c-bounded-investigation-001 \
  --output docs/results/p0-c-bounded-investigation-001
```

Changing the model, task, budget, adapter, evaluator, or candidate creates a
different experiment. It must use a new experiment ID and must not be compared
as a reproduction of this run.

## Public Evidence

- [Redacted experiment manifest](results/p0-c-bounded-investigation-001/experiment-manifest.json)
- [Raw task and comparison metrics](results/p0-c-bounded-investigation-001/raw-metrics.json)
- [Redacted normalized trace sample](results/p0-c-bounded-investigation-001/trace-sample.jsonl)
- [Concise machine-readable result](baselines/P0_C_BOUNDED_INVESTIGATION_AB.json)
- [Versioned candidate](candidates/P0_C_BOUNDED_INVESTIGATION.json)

The public manifest retains task, model, adapter, evaluator, policy, budget, and
source manifest digests. The trace sample retains event, run, trace, task, and
artifact identities while redacting credentials, absolute paths, task text,
Context text, artifact locations, and native payload references. Complete local
trajectories remain under ignored `.praxile/` state and are not published.

## Limitations

1. This is a one-task engineering acceptance run, not a leaderboard result or
   a statistically meaningful model-quality claim.
2. Neither arm produced a patch, so objective resolution is unknown and the
   SWE-bench evaluator did not execute.
3. The result demonstrates controlled injection, trace capture, diagnosis,
   invariant enforcement, and honest reporting; it does not demonstrate that
   this candidate improves coding success.
4. Monetary cost is unavailable because the selected model was not priced by
   the local LiteLLM configuration and the run explicitly used
   `ignore_errors` cost tracking.
5. The run used Docker Desktop on macOS arm64. A future multi-task benchmark
   should publish image digests and a Linux reference environment.
6. Trace byte deltas combine changed agent behavior and one additional Context
   event; they are not a causal estimate of tracing latency.

## P0 Conclusion

P0 establishes that Praxile can observe an external Base Agent, preserve
normalized evidence, run a fixed benchmark protocol, diagnose failures, test
one governed Context/Experience variable, reject unsupported improvement
claims, and export a reviewable public evidence package. Broader quality claims
require larger clean and continual evaluation tracks after P0.
