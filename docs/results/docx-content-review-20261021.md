# Post-hoc DOCX content review

This independent invocation of MiniMax-M3.1-Flash (reasoning_effort=low) examined
the six previously retained development artifacts. It is the same model family
as the executor, not an independently calibrated model. No official rubric was
used, no production memory changed, and no candidate was promoted.

Before the requests, all six DOCX hashes were checked against the published
[execution receipt](docx-six-arm-20261020.json). The source checklist was extracted
as OOXML body paragraphs and table rows. Fourteen packet rows plus the roles and
dates sections yielded sixteen requirements. They require preserving each row's
item, action, recipient, timing and compliance conditions. These are compound
requirements, not atomic facts or a calibrated scoring scale. They were fixed
before these review calls, but AFTER execution; this is post-hoc diagnosis only.
Extraction excludes headers, footnotes and visual layout. Complete requirement
coverage is not established. Source and output bodies were sent only to the judge.

## Results and failures

Six sequential requests were attempted, with 3000 output tokens and 90 seconds per
call, transport retries configured to zero. Two produced locally validated reviews:

| Artifact | Preserved | Missing | Contradicted | Recorded input/output tokens |
| --- | ---: | ---: | ---: | --- |
| First baseline | 11 | 4 | 1 | 4506 / 2624 |
| Composition candidate | 3 | 13 | 0 | 4346 / 2372 |

The other four requests abstained at the request stage. The then-current API did
not retain a safe exception category, so their exact cause cannot be reconstructed.
No retries were made to replace these failures. Their token usage is unknown,
not zero. The known subtotal is 8852 input / 4996 output tokens; the complete
review cost is unknown. No complete successful pair exists, so these counts must
not be used to rank candidates or calculate a paired improvement.

Even the valid reviews are LLM judgments. Exact-quote validation establishes that
citations exist, not that reasoning is correct. The counts signal content risks,
not independently verified omissions, official failure rates or statistical gains.

After all six reports persisted, the local harness exited with code 1 while calling
`close()` on SimpleHTTPTransport, which has no such method. No execution agent or
container was involved in this review. The temporary harness was corrected to use
an optional close method but was not rerun. Do not label the process successful.

## Follow-up

The review API now records a bounded failure category, response digest and finish
reason when available, and distinguishes logical request count from known usage.
These new diagnostics cannot retroactively explain the four failures. The
[public receipt](docx-content-review-20261021.json) retains unknowns, artifact hashes
and verdicts; original quote text stays in the local private review directory.
Next work remains request-failure diagnosis, calibrated source-to-output review,
behavior comparison, and frozen multi-task evaluation. This does not complete R2.

## Separate diagnostic request

One additional request used the exact first-candidate review input, same model,
low reasoning, 3000 output tokens, 90-second timeout and zero configured transport
retries. Its process exited cleanly. The provider returned a response with finish
reason `stop`, but local validation rejected the submission. No item verdict was
accepted. The [diagnostic receipt](docx-review-diagnostic-20261021.json) records this
as a separate call, not a replacement for the original failed review. It does not
explain the four original request failures. The exact validation defect was not
retained by that invocation, so do not infer whether it was JSON, citation or
coverage failure. No additional calls were made to obtain a passing response.

The API now projects bounded HTTP/attempt diagnostics from the nearest transport
exception cause, without exception messages or response bodies. Tests exercise
the actual Router/Provider wrapping path with a simulated HTTP 429, invalid fields
and cyclic causes. This is not evidence that the real earlier failures were 429s.

## Function submission trial

One further, separately recorded call used the same first-candidate input and
budget with explicit `response_mode=function`. MiniMax returned a named function
submission with finish reason `tool_calls`, but local item validation rejected it.
See [protocol trial receipt](docx-review-function-20261021.json). This demonstrates
transport submission, not valid review or semantic quality. The invocation retained
the coarse `invalid_item` code; subsequent code splits this into field/ID/verdict/
reason/quote diagnostics, but cannot retroactively identify which check failed.
No automatic repair, retries, candidate ranking or promotion followed this trial.

## Checkpoint-backed diagnosis

A separate single call retained its response in a private 0600 checkpoint under a
0700 directory. The checkpoint content hash matches the review response hash.
Offline inspection found only six of sixteen required items, with the sixth
missing `reason`. Completion usage was exactly the 3000-token budget even though
finish reason was `tool_calls`. Budget exhaustion is a plausible explanation,
not a proven provider-side truncation cause. Earlier unretained responses cannot
be diagnosed from this new sample.

The [sanitized receipt](docx-review-checkpoint-20261021.json) publishes counts,
missing known field names, hashes and usage, not the private response. Validation
correctly abstained. The API now supports an explicit checkpoint callback and
records returned/expected item counts plus a reported output-budget-reached flag.
That flag is not itself a quality verdict or proof of complete billing. Regression
tests verify that a `tool_calls` finish never bypasses local coverage checks.
Next implementation should partition requirements under a frozen total budget
and combine only complete item coverage, rather than retrying the whole document
until it passes. No such partitioned evaluation has run yet.

## Fixed-budget partition trial

The subsequent batch API froze four groups of four requirements, each allocated
2000 output tokens (8000 total). This changed budget is not a matched efficiency
comparison with earlier 3000-token requests. All groups used the same complete
text snapshots. Requirements and groups were frozen before any request.

The first two groups returned locally valid reviews for eight requirements. The
third returned four items but failed exact-citation validation; the fourth was
not called. Offline private-response inspection found noncontiguous quotes with
ellipsis and a whitespace mismatch. No quotes were repaired or accepted by fuzzy
matching. None of the three groups reached its output token allocation. This is
a distinct failure from the earlier incomplete submission at the token limit.

The [batch receipt](docx-review-batch-20261021.json) preserves the partial outcome:
three logical calls, 6000 requested output-token allowance, 9365 input and 4100
output tokens reported. The remaining eight requirements are unreviewed, not
passed or failed. No complete top-level result, official score or promotion was
issued. Next work is a source-bound citation interface that can represent multiple
literal spans without trusting model-reconstructed prose, followed by calibrated
evaluation. This trial does not complete content quality acceptance.

## Source-bound unit citation trial

An explicit `citation_mode=units` trial retained the four-by-2000 output budget,
same text snapshots and requirements. The controller supplied lossless 512-code-
point spans whose IDs contain the entire output-text digest and start offset.
Models selected IDs; the controller reconstructed cited text. Multiple spans are
represented separately rather than stitched into a fabricated continuous quote.

Three groups returned twelve items with valid IDs and complete local coverage.
The fourth logical request failed with `model_response_error` before a normalized
response or usage was available. The exact malformed-envelope/function issue is
not established. The run therefore abstained with four unreviewed requirements;
it did not combine twelve valid items into a complete result. No retries ran.

The [unit-citation receipt](docx-review-units-20261021.json) reports the known
subtotal for three responses: 10909 input / 4470 output tokens. The fourth call's
usage and full run cost are unknown. This confirms the citation interface worked
for three responses, not semantic correctness, reliability, or a quality gain
over earlier stochastic calls. Complete and calibrated content acceptance remains
unfinished. Raw checkpoints and reconstructed source text remain private.

## Invalid submission usage diagnostic

One new call used exactly the prior fourth group's input and function contract,
with the same 2000-token budget, low reasoning and no transport retry. The new
provider diagnostic reported `submission_arguments_invalid`, finish reason
`length`, and reported output usage reaching the budget. The
[receipt](docx-review-envelope-20261021.json) retains the failed call's token
usage through the real provider/router/reviewer path. No body is published.

This directly identifies a truncated/invalid submission in the new call, not the
unretained prior response. It remains a separate diagnostic; it is not joined to
the earlier twelve items as a completed batch. The reviewer's ledger now records
received-but-invalid submissions as failures with known usage, or unknown where
fields are absent/invalid. Other ModelError consumers have not all been migrated.
No automatic retries, budget escalation or promotion followed.

## Complete development review with explicit allocations

A new run froze allocations `[1800, 1400, 1800, 3000]` before execution, keeping
the total requested output allowance at 8000. The allocation was chosen using
development usage observations: it is tuning, not held-out validation or a
preregistered efficiency result. The source/output snapshots, sixteen requirements,
model and unit-citation protocol were unchanged. No retry or budget transfer ran.

All four groups returned complete, locally validated reviews. None reached its
output allocation. LLM verdicts were 12 preserved, 3 missing and 1 uncertain.
Thus the review workflow completed, but the artifact did not receive an
all-requirements-preserved judgment. Semantic accuracy remains uncalibrated;
these counts are not an official task score or independently verified omissions.

The [allocated review receipt](docx-review-allocation-20261021.json) retains all
sixteen verdicts and snapshot-bound reference IDs without publishing source spans
or model prose. The batch ledger covers four recorded logical calls: 14698 input
and 5969 output tokens; money remains unknown. It excludes prior development,
design, execution and review attempts and does not independently attest provider
billing or hidden retry usage. No complete baseline/candidate semantic pair was
evaluated under this allocation. No candidate was promoted.

## Fresh paired review attempt

A later run independently reviewed both retained first-pair artifacts with the
same source, sixteen requirements, model/options, review-code identity, unit
citation protocol and `[1800,1400,1800,3000]` allocation. Both artifact hashes were
checked against the six-arm receipt. No earlier review result was reused as an
arm. This remains a one-task, post-hoc development comparison; allocation had
already been tuned on development observations.

Baseline completed three groups, then its fourth transport call timed out at
90113 ms, leaving that call's usage unknown. Candidate completed one group, then
its second returned `length` with invalid function arguments at its allocation.
Both arms abstained; no retry or increased budget followed. The
[paired receipt](docx-content-pair-20261021.json) records `inconclusive` because
neither review is complete. Earlier successful candidate review is not substituted.

The new comparator checks plan identities, conditions, group/combined consistency
and requirement coverage, but does not authenticate callers or calibrate semantic
judgments. It cannot turn these partial results into a score or a promotion. This
negative repeat shows that one completed review did not establish reliability.
