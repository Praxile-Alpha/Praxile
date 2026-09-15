# P1 Control-Plane Fixture Acceptance

Status: completed contract acceptance

This package closes the deterministic acceptance portion of the Praxile V2 P1
checklist. It was generated with `FixtureAgentAdapter`; it validates schemas,
trace evidence, conservative attribution, controlled comparison, six-gate
promotion, atomic active-pointer replacement, and rollback.

It deliberately makes no claim that a real model solves more tasks. Real-model
quality remains visible in separately published benchmark experiments, including
the inconclusive MiniMax M3 held-out result.

## Evidence

- `context-source-trace.json` records selected and unselected sources, token
  utilization, compression profile, before/after token counts, and reasons.
  Token counts state whether they were reported by the compressor or estimated.
- `skill-activation-ab.json` separates injection, explicit reference, outcome
  comparison, and causal credit.
- `subagent-ab.json` compares no-subagent and isolated-subagent fixture arms and
  requires start/end plus isolated merge-gate evidence.
- `promotion-rollback.json` contains the candidate, all six gates, promoted
  registry snapshot, and rolled-back registry snapshot.

Regenerate the JSON artifacts with:

```bash
python -B scripts/generate_p1_acceptance.py
```
