# Experience Governance

Praxile turns agent runs into repository-local experience assets. Over time, any experience base can become noisy: two memories may say the same thing, an old failure pattern may no longer apply, a broad skill may be weaker than a newer project-specific one, or a rejected generic proposal may keep reappearing. Experience governance is the review loop that keeps `.praxile/` useful.

Praxile is not model self-training and not autonomous self-modification. Governance outputs are proposals. They are auditable diffs and only become active after human approval.

## What Gets Governed

Active assets participate in retrieval by default:

- `memory/`: project/user/decision notes
- `skills/*/SKILL.md`: reusable project workflows
- `evals/`: checklists and regression cases
- `experience/failures/`: failure patterns
- `experience/patterns/`: cross-run project pattern cards mined from repeated episodes
- `rules/`: harness rules, architecture gates, and frozen boundaries

Inactive assets remain on disk and in the SQLite index for audit:

- `deprecated`: still visible, excluded from normal retrieval
- `superseded`: replaced by a better asset through `replaced_by`
- `archived`: obsolete enough to keep only as history
- `active`: loaded again after manual or proposal-based reactivation

## Consolidation Loop

Run a summary first:

```bash
praxile consolidate --all --summary
```

Example shape:

```text
Experience consolidation summary:
- duplicates: 2
- stale: 1
- conflicts: 0
- low_value: 1
```

Then generate proposal-only cleanup:

```bash
praxile consolidate --all --stale-days 90
praxile review --type asset_merge
praxile review --type asset_rewrite
```

Example proposal shape:

```text
prop_... [asset_merge] Supersede duplicate experience assets for `parser-fixture-reset`
Governance preview:
- metadata diff `experience/failures/parser-old.md`: replaced_by, status, superseded_at, superseded_reason
- content diff `experience/failures/parser-new.md`: append 28 line(s)
- retrieval impact: Experience retrieval for the targeted asset lifecycle metadata.
- rollback path: praxile rollback prop_...
```

An `asset_merge` keeps one canonical asset active, marks duplicates as `superseded`, and appends consolidated evidence such as `applies_when`, `does_not_apply_when`, `failure_signature`, `verification_commands`, `fix_actions`, and outcome counts.

## Project Pattern Cards

`project_pattern` proposals are stronger than a one-run memory note. They are mined from repeated episodes and written to `experience/patterns/` only after review. A single episode may produce a low-confidence candidate so reviewers can preserve a promising lesson, but the card should not become high-confidence until later episodes, user feedback, verification commands, or accepted proposal outcomes corroborate it. A good card includes:

- `Claim`: the project-specific lesson as a testable hypothesis
- `Applies When`: files, commands, signatures, or loaded assets that make the lesson relevant
- `Does Not Apply When`: anti-scope that prevents overgeneralization
- `Evidence`: concrete source facts from trajectories and episodes
- `Failure Signatures`: recurring errors, blocked actions, or symptoms
- `Fix Strategy`: observed repair shape, not a blind patch
- `Verification Commands`: commands that proved the repair in source episodes
- `Counterexamples`: similar episodes that failed or needed a different fix
- `Source Episodes`: episode/task IDs for audit
- `Confidence`: score plus rationale
- `Expected Future Use`: how retrieval should use the card next time

If a card has weak evidence, vague scope, or no verification, edit or reject it instead of accepting a generic rule into retrieval.

Pattern mining uses multiple dimensions: failure signature overlap, affected files, verification commands, fix-action similarity, task type, loaded asset overlap, accepted/rejected proposal similarity, and user feedback. Counterexamples are first-class evidence. A negative run, rejected similar proposal, or harmful feedback should lower confidence or add anti-scope instead of being buried in the notes.

When `semantic_judges.enabled=true`, Pattern Mining can ask the local `pattern_mining` model role to judge whether candidate episodes share a root cause, fix strategy, or verification path even when exact signatures differ. The semantic result can improve the claim and score, but the card is still a proposal until reviewed.

## Review Decisions

Accept when:

- the proposal is low risk and project-local
- evidence cites concrete files, commands, failure signatures, or usage outcomes
- the scope and anti-scope are clear
- the diff improves retrieval quality without changing runtime safety policy

Reject when:

- the proposal is generic advice with no project-specific signal
- the same idea has already been rejected
- evidence is weak or unrelated to the source trajectory
- accepting would pollute future retrieval

Edit when:

- the idea is useful but wording is too broad
- an `asset_rewrite` draft needs human cleanup
- a skill needs tighter triggers or a better anti-scope

Merge when:

- two assets describe the same failure signature, command, or file pattern
- one asset has stronger positive outcomes and can become canonical
- the duplicate is complementary but should not load separately

Archive when:

- an already retired asset is obsolete
- the old guidance is useful only as audit history
- reactivation should require explicit review

Reactivate when:

- a retired asset becomes relevant again
- a mistaken archive/deprecation needs to be undone
- a future task proves the old experience still helps

## Duplicate Warnings

Review may show:

```text
High duplicate confidence: similar_asset=.praxile/experience/failures/parser.md; reason=same failure signature: assertionerror; warning_id=...
Medium duplicate confidence: similar_asset=.praxile/memory/parser.md; reason=affected file overlap: praxile/parser.py
Possible overlap: similar_asset=.praxile/memory/project.md; reason=title term overlap: parser, fixture
```

High confidence should usually be inspected before accepting. Medium confidence means compare scope. Possible overlap is informational unless the content really duplicates an existing paragraph.

Interactive review records how users handle duplicate warnings. If a similar warning is repeatedly ignored or accepted anyway, future warnings are downgraded so review does not become noisy.

## Useful Commands

```bash
praxile memory list --include-inactive
praxile asset status .praxile/memory/project.md
praxile asset diff .praxile/memory/old.md --with .praxile/skills/parser-repair/SKILL.md
praxile asset deprecate .praxile/memory/old.md --reason "covered by parser skill"
praxile asset supersede .praxile/memory/old.md --replaced-by .praxile/skills/parser-repair/SKILL.md
praxile asset archive .praxile/experience/failures/old.md --reason "obsolete"
praxile asset reactivate .praxile/memory/old.md --reason "useful again"
```

`praxile asset status` shows retrieval participation, lifecycle reasons, replacement targets, usage counts, positive/negative outcomes, and recent lifecycle history. Superseded assets print a suggested `praxile asset diff` command.

## Maintenance Rhythm

For active repositories:

- run `praxile review --summary` after each task batch
- run `praxile reject --low-confidence --reason "too generic"` when low-value proposals pile up
- run `praxile consolidate --all --summary` weekly or before releases
- accept governance proposals one by one when they touch rules, boundaries, skills, or rewrites
- prefer edit over accept when a proposal is directionally right but too broad

The goal is not to maximize memory size. The goal is a small, specific, explainable experience base that improves the next similar task.
