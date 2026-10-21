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
