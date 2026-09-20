# P2-B: Experience Representation Router

The semantic activation gate answers **whether** one candidate applies. The representation router then answers **how** to inject it: `none`, `raw_episode`, `summary_memory`, `skill`, or `failure_pattern`. It never changes the baseline arm. Existing candidates without `representation_options` retain their previous single-context behavior.

## Implemented Scope

- A candidate may provide multiple text representations of the **same** evidence-backed experience and a versioned routing profile. The candidate digest freezes all options.
- The deterministic router runs only after semantic activation. It records all eligible forms, estimated context units (approximately four characters per unit), scores, selected form, reasons, and input features in the A/B manifest and report.
- Inputs: task state dependency (task metadata `context_state_dependency`, otherwise profile default), task-specific `context_compressibility` or profile default, candidate `evidence_density`, and the smaller of candidate `token_budget` and task `context_token_budget`. Optional task `experience_intent` is `neutral`, `procedure`, or `failure`.
- Evidence density below 0.45, insufficient budget, or failed semantic activation selects `none`. No alternative content enters the adapter policy. `CONTEXT_REPRESENTATION` is recorded separately from `CONTEXT_ACTIVATION` and `CONTEXT_INJECT`.
- Only the selected text is passed to the adapter. Public exports redact every representation option. The immutable A/B manifest and invariant checker include the routing decision, so a resume cannot silently change forms.

The scoring policy is `praxile.experience_router.v1`. It favors raw episodes for high state dependency and low compressibility, summaries for compressible knowledge, skills for procedural tasks, and failure patterns for failure-focused tasks. All eligible scores and estimated costs are persisted for audit. These are deterministic **routing heuristics**, not learned quality judgments or proof that a form improves task success; controlled A/B evaluation remains necessary.

## Example

See [P2_B_REPRESENTATION.example.json](candidates/P2_B_REPRESENTATION.example.json). It can be passed as the candidate to `praxile eval ab` after replacing the example repository, evidence reference, source task, and task signals with real values. For task-specific routing, a local task-set JSON may include metadata such as:

```json
{
  "context_state_dependency": 0.9,
  "context_compressibility": 0.2,
  "context_token_budget": 120,
  "experience_intent": "failure"
}
```

## Normal Runs

`praxile run` now applies the same two-stage decision after project retrieval. It derives only forms supported by each indexed, active asset: a bounded `summary_memory` from the indexed snippet; `skill` from a project-local `SKILL.md`; `failure_pattern` from a project-local failure asset; and a `raw_episode` **step excerpt** from a project-local trajectory (task, result summary, action type and status only, never raw tool observations). It does not invent a skill or failure pattern from an unrelated memory. Files resolving outside `.praxile/` are rejected.

The runtime uses the analyzed task type as a deterministic state-dependency and intent signal. Evidence density is bounded by asset confidence and source linkage; compressibility is estimated from the available text lengths. Explicit asset-sidecar `applies_to` scope takes precedence; older assets fall back to matched retrieval terms, which is a weaker semantic signal. Missing task signals, low density, or exhausted budget select `none`. Mandatory architecture/safety rules retain their existing loading behavior and remain available to task-risk analysis; that analysis no longer passes unselected experience snippets to an optional planning model. The trajectory stores `experience_representation.decisions`, while `loaded_assets[].used_in_prompt` and the SQLite activation funnel distinguish retrieval from actual prompt injection. An architecture-gated task that never constructs an agent prompt does not receive false injection credit. Checkpoints freeze the selected text; a resumed run cannot silently replace the task text. See the `context.experience_representation_enabled` and `context.experience_representation_budget_units` configuration keys.

These choices are auditable routing heuristics, not a demonstrated performance gain. No LLM-generated projection is automatically promoted, and held-out A/B testing is still required to claim improvement. The current raw episode is deliberately a limited structured excerpt, not the full trajectory or arbitrary command output.
