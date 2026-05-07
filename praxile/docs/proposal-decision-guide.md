# Proposal Decision Guide

Praxile learns only through reviewed proposals. A proposal is a draft update to repository-local experience, not proof that the agent is right.

Praxile is not model self-training, not a fully autonomous coding agent, and not an automatic architect. Durable updates remain local to `.praxile/` and require approval.

## Quick Rules

- Accept low-risk, project-local proposals with concrete files, commands, failure signatures, verification output, or positive outcome history.
- Reject generic or low-evidence proposals.
- Edit useful drafts that are too broad.
- Merge assets that describe the same project lesson.
- Archive obsolete retired assets.
- Reactivate retired assets only when they should load again.

## Type Guide

| Proposal type | Default action | Accept when | Reject when |
| --- | --- | --- | --- |
| `memory_update` | accept / edit | It names concrete project evidence. | It is generic advice. |
| `failure_pattern` | accept | It has signature, reproduce command, fix actions, and verification. | It cannot be reproduced. |
| `skill_create` | inspect / edit | The workflow will recur and includes trajectory evidence. | It is only a generic checklist. |
| `eval_case` | accept / edit | It captures a concrete regression or human acceptance checklist. | It is too vague to verify. |
| `harness_rule` | inspect | It protects a recurring runtime behavior. | It is over-broad or bypasses safety. |
| `architecture_gate` | inspect | It protects shared contracts, auth/session, routing, storage, or migrations. | It blocks normal local work. |
| `frozen_boundary` | inspect | The boundary has system-wide impact. | It freezes local implementation detail. |
| `asset_merge` | inspect | Assets share the same failure, command, file pattern, or lesson. | Assets apply to different contexts. |
| `asset_rewrite` | edit / inspect | A weak asset has positive outcomes and can be made specific. | It removes scope or evidence. |
| `asset_archive` | inspect | A retired asset is obsolete but auditable. | It may still be useful soon. |
| `asset_reactivate` | inspect | A retired asset should load again. | The reason is unclear. |

High-risk proposals should not be batch accepted. Low-confidence proposals should usually be rejected or edited unless similar accepted assets have positive outcomes.

Duplicate warnings are advisory: high confidence means compare first, medium means inspect scope, possible overlap is informational.
