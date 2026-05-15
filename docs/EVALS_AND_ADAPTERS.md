# Evals And Adapters

## Generic JSONL Adapter

Praxile's first external adapter is intentionally narrow:

```bash
praxile interop import-jsonl external-trace.jsonl --generate-proposals
praxile interop import-jsonl external-trace.jsonl --write-proposals
```

It accepts JSONL rows such as:

```json
{"event":"task","task_id":"external_task","task":"Fix retry timeout"}
{"event":"action","tool":"run_command","command":"python -m pytest","status":"success"}
{"event":"observation","output":"1 passed","status":"success"}
{"event":"result","status":"completed","summary":"Fixed"}
```

The adapter converts these rows into a Praxile trajectory and can optionally run experience proposal generation. It does not sync external memory, install external skills, or let another framework write `.praxile/` directly.

## Eval Suites

Eval suites are JSON files that run proposal generation against known trajectories:

```json
{
  "name": "basic proposal generation",
  "cases": [
    {
      "name": "extract retry failure pattern",
      "input": {
        "trajectory_file": "fixtures/api_retry_trace.json"
      },
      "expected": {
        "proposal_type": "failure_pattern",
        "keywords": ["retry", "timeout", "backoff"]
      },
      "metrics": ["proposal_type_match", "keyword_hit"]
    }
  ]
}
```

Run:

```bash
praxile eval run eval_suites/basic.json --output eval-report.json
```

Supported first-release metrics:

- `proposal_generated`
- `proposal_type_match`
- `keyword_hit`
- `min_proposals`

Eval reports include per-case scores, generated proposal summaries, and an average score. They do not accept or apply proposals.
