# Praxile V2 Eval Runner

Status: P0-B implementation and public fixed-model baseline accepted
Last updated: 2026-09-11

## Boundary

`praxile.eval.v2` is the benchmark control plane. It does not replace the legacy `EvalRunner`, which continues to evaluate Praxile proposal generation and reviewed project commands.

The V2 path is:

```text
SWE-bench JSON/JSONL
  -> versioned EvalTaskSet
  -> detached Git worktree at base_commit
  -> AgentAdapter V2
  -> normalized EventStore trace + content-hashed patch artifact
  -> official SWE-bench prediction JSONL
  -> upstream Docker evaluator
  -> durable per-task result
  -> baseline report
```

Reference patches, test patches, expected tests, and image assets remain in the evaluator-owned task record. `EvalTask.to_adapter_task()` excludes them, so the base agent receives only the issue, repository identity, base commit, and explicitly declared public metadata.

## Install

The benchmark extra installs mini-SWE-agent and the official SWE-bench harness. Docker is also required by SWE-bench evaluation.

```bash
python -m pip install -e ".[benchmark]"
```

## Run

By default the loader reads the configured public dataset from Hugging Face. Selection is deterministic for a fixed seed.

```bash
praxile eval benchmark \
  --dataset-name SWE-bench/SWE-bench_Lite \
  --development-size 5 \
  --seed praxile-p0 \
  --model openai/gpt-5 \
  --cost-tracking default \
  --timeout 1800 \
  --step-limit 50 \
  --max-cost 2 \
  --run-id p0-baseline-001
```

An official JSON or JSONL export may be passed as the positional argument for offline or mirrored operation:

```bash
praxile eval benchmark ./benchmarks/swe-bench-lite.jsonl \
  --development-size 5 \
  --seed praxile-p0 \
  --model openai/gpt-5 \
  --cost-tracking default \
  --timeout 1800 \
  --step-limit 50 \
  --max-cost 2 \
  --run-id p0-baseline-001
```

Resume after interruption without re-running tasks that already have a result under the same immutable manifest:

```bash
praxile eval benchmark ./benchmarks/swe-bench-lite.jsonl \
  --development-size 5 \
  --seed praxile-p0 \
  --model openai/gpt-5 \
  --cost-tracking default \
  --timeout 1800 \
  --step-limit 50 \
  --max-cost 2 \
  --run-id p0-baseline-001 \
  --resume
```

For a model that LiteLLM can call but cannot price, pass
`--cost-tracking ignore_errors`. Praxile records that policy in the immutable
manifest. Monetary cost is not a valid budget signal for that run, so retain a
wall-clock timeout and do not use its cost value for comparisons.

For local fixtures or repository mirrors, add `--source owner/repo=/absolute/path/to/repo`. Every task still runs in its own detached worktree; the source checkout is not edited.

## Persistence

Each run writes under `.praxile/eval/v2/runs/<RUN_ID>/`:

- `manifest.json`: write-once reproducibility inputs and digest;
- `tasks/<TASK_ID>/prediction.jsonl`: official `instance_id`, `model_name_or_path`, `model_patch` handoff;
- `tasks/<TASK_ID>/agent-artifacts/`: hash-verified copies that survive worktree cleanup;
- `tasks/<TASK_ID>/evaluator/`: evaluator stdout, stderr, prediction, and discovered upstream report;
- `tasks/<TASK_ID>/result.json`: durable task status, trace IDs, repository revision, metrics, and error;
- `report.json`: baseline or experiment aggregate.

Canonical agent events and artifact metadata are stored by `EventStore` as append-only JSONL plus the shared SQLite index. Large patch, native trajectory, and evaluator payloads stay file-backed.

## Metrics

P0 metrics are computed from normalized events and evaluator evidence: resolution, input/output/cache tokens, model cost, wall latency, reported model latency, model calls, tool calls, rejected-response retries, failed-then-passed verification recoveries, and human interventions. Missing evaluator evidence produces `resolved: null`; it is not counted as success or failure.

## Upstream contract

The handoff follows the official SWE-bench prediction fields `instance_id`, `model_name_or_path`, and `model_patch`, and invokes `swebench.harness.run_evaluation` one instance at a time. See the [official evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/) and [upstream runner](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/run_evaluation.py).

## Acceptance status

Schema, leakage boundary, deterministic selection, Git isolation, manifest immutability, official command/result integration, timeout normalization, error continuation, Event Store ingestion, metric aggregation, and resume behavior have automated coverage.

The checked-in [P0-B public baseline](baselines/P0_B_SWEBENCH_BASELINE.json)
records one fixed external model, mini-SWE-agent version, public task, timeout,
normalized trace metrics, artifact digests, and the official Docker evaluator
decision. It accepts the runner pipeline, not model quality: the sample has one
task, the model was not priced by LiteLLM, and that historical run had no step
limit. New runs default to `--step-limit 50`.
