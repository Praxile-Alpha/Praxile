# Experience Governance

Praxile turns agent runs into repository-local experience assets. Over time, any experience base can become noisy: two memories may say the same thing, an old failure pattern may no longer apply, a broad skill may be weaker than a newer project-specific one, or a rejected generic proposal may keep reappearing.

Governance outputs are proposals. They are auditable diffs and only become active after human approval. Praxile is not model self-training and not autonomous self-modification.

## Loop

```bash
praxile consolidate --all --summary
praxile consolidate --all --stale-days 90
praxile review --type asset_merge
praxile review --type asset_rewrite
```

`asset_merge` keeps one canonical asset active, supersedes duplicates, and appends consolidated evidence such as applies_when, does_not_apply_when, failure_signature, verification_commands, fix_actions, and outcome counts.

## Project Pattern Cards

`project_pattern` proposals are mined from episodes and written to `experience/patterns/` only after review. A single episode may produce a low-confidence candidate, but confidence should rise only when later episodes, user feedback, verification commands, or accepted proposal outcomes corroborate it.

A strong card includes Claim, Applies When, Does Not Apply When, Evidence, Failure Signatures, Fix Strategy, Verification Commands, Counterexamples, Source Episodes, Confidence, and Expected Future Use. Pattern mining weighs failure signature overlap, affected files, verification commands, fix-action similarity, task type, loaded asset overlap, accepted/rejected proposal similarity, and user feedback. Counterexamples should lower confidence or add anti-scope instead of being buried in notes.

When `semantic_judges.enabled=true`, Pattern Mining can ask the local `pattern_mining` model role to judge whether candidate episodes share a root cause, fix strategy, or verification path even when exact signatures differ. The semantic result can improve the claim and score, but the card is still a proposal until reviewed.

## Decisions

- Accept low-risk project-local proposals with concrete file, command, failure, or outcome evidence.
- Reject generic proposals without project-specific signal.
- Edit useful but over-broad rewrite or skill drafts before accepting.
- Merge assets that describe the same command, file, or failure signature.
- Archive obsolete retired assets that should stay auditable but inactive.
- Reactivate retired assets when a future task proves they are useful again.

## Duplicate Warnings

Review labels overlap as `High duplicate confidence`, `Medium duplicate confidence`, or `Possible overlap`. High confidence should be inspected before accepting. Medium confidence means compare scope. Possible overlap is informational unless the content really duplicates an existing paragraph.

Interactive review records duplicate-warning decisions. If similar warnings are repeatedly ignored or accepted anyway, future warnings are downgraded so review stays useful.

## Commands

```bash
praxile memory list --include-inactive
praxile asset status .praxile/memory/project.md
praxile asset diff .praxile/memory/old.md --with .praxile/skills/parser-repair/SKILL.md
praxile asset supersede .praxile/memory/old.md --replaced-by .praxile/skills/parser-repair/SKILL.md
praxile asset archive .praxile/experience/failures/old.md --reason "obsolete"
praxile asset reactivate .praxile/memory/old.md --reason "useful again"
```

The goal is a small, specific, explainable experience base that improves the next similar task without silently changing long-term behavior.
