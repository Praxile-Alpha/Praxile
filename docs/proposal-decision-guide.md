# Proposal Decision Guide

Praxile learns only through reviewed proposals. A proposal is not proof that the agent is right; it is a draft update to repository-local experience that a human can accept, edit, reject, merge, archive, or reactivate.

Praxile is not model self-training, not a fully autonomous coding agent, and not an automatic architect. Durable updates remain local to `.praxile/` and require approval.

## Quick Decision Rules

Accept when the proposal is low risk, project-local, and backed by concrete evidence such as files, commands, failure signatures, verification output, or positive outcome history.

Reject when the proposal is generic, low-evidence, duplicated by better active experience, or based on a trajectory that did not actually prove the lesson.

Edit when the idea is useful but the wording, scope, trigger, or anti-scope is too broad.

Merge when two active assets describe the same project lesson and one should become canonical.

Archive when an already retired asset is obsolete but still worth keeping for audit history.

Reactivate when a retired asset becomes useful again and should participate in retrieval.

## Proposal Type Table

| Proposal type | Recommended action | Accept when | Reject when |
| --- | --- | --- | --- |
| `memory_update` | accept / edit | It names concrete project files, commands, decisions, or failure signals. | It is generic advice or should be user-global rather than project-local. |
| `failure_pattern` | accept | It has a failure signature, reproduce command, affected files, fix actions, and verification commands. | It cannot be reproduced, has no clear signature, or came from an unreviewed failed run. |
| `skill_create` | inspect / edit | The same workflow is likely to recur and the skill includes trajectory evidence. | It is only a generic checklist with no project-specific trigger. |
| `eval_case` | accept / edit | It captures a concrete regression or human acceptance checklist future runs should use. | It duplicates an existing eval or is too vague to verify. |
| `harness_rule` | inspect | It changes runtime behavior for a clearly recurring task class. | It is over-broad, bypasses safety, or would surprise future users. |
| `routing` | inspect | It records concrete model availability, privacy, cost, or high-risk routing evidence. | It silently moves unrelated work to expensive or unsafe routes. |
| `architecture_gate` | inspect | The task touches shared contracts, auth/session, routing, storage, migrations, or frozen boundaries. | It blocks normal local work because the scope is too broad. |
| `frozen_boundary` | inspect | The boundary has system-wide impact and future changes should pause for review. | The proposal freezes a local implementation detail that should remain flexible. |
| `asset_merge` | inspect | Assets share the same failure signature, command, file pattern, or project lesson. | Assets only look similar but apply to different contexts. |
| `asset_rewrite` | edit / inspect | A weak asset has positive outcomes and can be made more specific. | The rewrite removes important scope, evidence, or rollback context. |
| `asset_deprecate` | accept / inspect | The asset is low-value, stale, superseded, or harmful to retrieval. | It is still actively useful for recent tasks. |
| `asset_archive` | inspect | A retired asset is obsolete but should remain auditable. | It may still need to be reactivated soon. |
| `asset_reactivate` | inspect | A retired asset has become relevant and should load again. | The reason is unclear or it conflicts with the replacement asset. |

## High-Risk Proposals

High-risk proposals include architecture gates, frozen boundaries, routing policy, and broad harness rules. Do not batch accept them. Review:

- what future tasks will be blocked or rerouted
- whether the scope is narrow enough
- what evidence triggered the proposal
- the rollback command
- whether a local memory or skill would be safer than a runtime rule

Accept only when the proposal protects a real shared boundary or recurring high-risk behavior.

## Low-Confidence Proposals

Low-confidence proposals should usually be rejected or edited. Keep them only when similar accepted assets have positive outcomes or the proposal contains a useful draft that can be tightened.

Useful low-confidence edits include:

- add exact files, commands, and failure signatures
- add `applies_when` and `does_not_apply_when`
- narrow the target skill trigger
- remove global-sounding advice
- add the human acceptance requirement for UX-sensitive work

## Duplicate Warnings

Duplicate warnings are advisory. Praxile labels them as:

- `High duplicate confidence`: same target, same failure signature, or same command plus file evidence
- `Medium duplicate confidence`: strong file/content overlap
- `Possible overlap`: title or term similarity that needs human comparison

For high confidence, compare the similar asset before accepting. For medium confidence, check whether scopes differ. For possible overlap, accept may still be fine if the new proposal adds a distinct use case.

When interactive review records repeated `ignored` or `accepted_anyway` decisions for a warning, Praxile lowers future warning strength so review stays focused.

## Editing Before Accept

Use interactive review:

```bash
praxile review --interactive
```

Choose `e` to open the proposal in your editor. Keep the same `proposal_id`, then accept the edited proposal. This is especially useful for `skill_create` and `asset_rewrite`, where a small human edit can turn a useful draft into durable project experience.

## Rollback

Every accepted proposal prints a rollback command:

```bash
praxile rollback <PROPOSAL_ID>
```

Use rollback when an accepted memory pollutes retrieval, a skill fires too broadly, a governance asset blocks normal work, or an archive/reactivation decision was mistaken.
