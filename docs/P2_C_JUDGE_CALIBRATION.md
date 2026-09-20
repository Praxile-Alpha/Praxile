# P2-C: Judge Calibration

Praxile does not treat an LLM's assessment of its own run as verification. Every completed run now records five separate, versioned fields:

- `self_judgment`: the optional LLM judge score, recommendation, model, prompt version, and reasons.
- `verifier_outcome`: objective test, regression, safety, and spec-compliance evidence.
- `next_task_delta`: later-run observations made only when an asset sourced from this task was actually injected.
- `judgment_calibration`: prediction/verifier agreement, absolute error, false-positive, and false-promotion flags.
- `transfer_effect`: an aggregate of later observational deltas. It is explicitly not a causal claim.

The JSON trajectory remains the run-level audit artifact. SQLite indexes the same judge observation for aggregation and later transfer updates. Use:

```bash
praxile judge metrics
praxile judge metrics --json --include-observations
```

The reported metrics are:

- `precision`: verified-positive runs divided by all self-judged positive runs.
- `calibration_error`: mean absolute difference between self-judgment score and objective verifier score on comparable runs.
- `false_promotion_rate`: self-judged `accept` recommendations rejected by the verifier, divided by all comparable `accept` recommendations.

## Promotion Boundary

A proposal may still enter the review inbox when objective verification is unavailable, because it can contain useful evidence or identify a failure. Its `promotion_eligibility` is nevertheless false. Self-judgment can prioritize review, but only a passing objective verifier outcome can grant promotion eligibility, and human approval remains mandatory. Harness-component promotion additionally requires isolated validation.

## Transfer Boundary

When a later task injects accepted experience, Praxile compares the source run's verifier score with the later run's verifier score and records the delta. This is useful monitoring evidence, not proof that the experience caused the result. Controlled A/B evaluation remains necessary for causal promotion claims.

Controlled mutation suites continue to run through `praxile judge calibrate`. Their reports now also include calibration error and false-promotion rate, and the SQLite calibration index stores those metrics.
