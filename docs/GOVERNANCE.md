# Experience Governance

Praxile connects spec-driven intent with evidence-driven experience. It turns agent runs into repository-local experience assets. Over time, any experience base can become noisy. Experience governance is the review loop that keeps `.praxile/` useful, clean, and safe.

Praxile is **not** model self-training and **not** autonomous self-modification. Governance outputs are proposals. They are auditable diffs and only become active after human approval.

---

## 1. What Gets Governed

**Active assets** participate in retrieval by default:
- `memory/`: project, user, and decision notes
- `skills/*/SKILL.md`: reusable project workflows
- `evals/`: checklists and regression cases
- `experience/failures/`: failure patterns
- `experience/patterns/`: cross-run project pattern cards
- `rules/`: harness rules, architecture gates, and frozen boundaries

**Inactive assets** remain on disk and in the SQLite index for audit:
- `deprecated`: still visible, excluded from normal retrieval
- `superseded`: replaced by a better asset
- `archived`: obsolete enough to keep only as history

---

## 2. Proposal Decision Guide

A proposal is a draft update to repository-local experience. You can `accept`, `edit`, `reject`, `merge`, `archive`, or `reactivate` it.

### Quick Decision Rules

- **Accept**: Low risk, project-local, and backed by concrete evidence (files, commands, failure signatures).
- **Reject**: Generic, low-evidence, duplicated by better active experience, or based on a trajectory that didn't actually prove the lesson.
- **Edit**: Useful idea, but the wording, scope, or anti-scope is too broad.
- **Merge**: Two active assets describe the same project lesson; one should become canonical.
- **Archive**: An asset is obsolete but worth keeping for audit history.
- **Reactivate**: A retired asset becomes useful again.

### High-Risk Proposals
Architecture gates, frozen boundaries, routing policies, and broad harness rules are high-risk. 
- Do not batch accept them.
- Review what future tasks will be blocked.
- Ensure the scope is narrow enough.
- Check if a simple local memory would be safer than a strict runtime rule.

### Editing Before Accept
Use interactive review (`praxile review --interactive`). Choose `e` to open the proposal in your editor. Tighten the scope, add anti-scopes, or specify the trigger, then accept the edited proposal.

### Rollback
Every accepted proposal prints a rollback command:
```bash
praxile rollback <PROPOSAL_ID>
```
Use rollback when an accepted memory pollutes retrieval or a governance asset blocks normal work.

---

## 3. Consolidation Loop

Over time, you should consolidate overlapping or stale assets.

**Run a summary:**
```bash
praxile consolidate --all --summary
```

**Generate proposal-only cleanup:**
```bash
praxile consolidate --all --stale-days 90
praxile review --type asset_merge
```
An `asset_merge` keeps one canonical asset active, marks duplicates as `superseded`, and appends consolidated evidence.

---

## 4. Audit & Compliance

Praxile audit commands are read-only governance surfaces over the local `.praxile/` source of truth.

### What To Export
- `praxile audit run latest --json`: Explains task analysis, model route, actions, reward, and generated proposals.
- `praxile audit proposal prop_abc123 --json`: Explains evidence, review state, target files, and applied changes.
- `praxile audit asset .praxile/memory/project.md --json`: Explains where an asset came from and how it has been used.
- `praxile audit bundle --redaction strict --output bundle.json`: Summarizes recent runs, pending proposals, and graph status.
- `praxile audit bundle --include-reflect --redaction strict --output bundle.json`: Adds latest Reflect governance reports to release evidence.
- `praxile reflect --ci`: Runs scheduled Reflect governance, writes artifacts, and returns a policy exit code.

### CI Governance Gate (`audit check`)
Use `audit check` as a release-time gate to ensure your team is managing AI experience properly:
```bash
praxile audit check --strict --rebuild-graph --redaction strict
```
**Default failures**: Constitution is incomplete, or high-risk proposals are pending.
**Strict failures**: *Any* pending proposals, missing experience graph, or a failed latest run.

### Recommended Release Flow
1. Run project verification commands.
2. Run `praxile spec verify latest` to check compliance against attached specs.
3. Review pending proposals with `praxile review --interactive`.
4. Rebuild relationship evidence with `praxile graph status --rebuild`.
5. Run `praxile reflect --ci` for experience-quality drift.
6. Run `praxile audit check --strict --redaction strict` in CI.
7. Archive `praxile audit bundle --include-reflect --redaction strict` as release evidence when Reflect governance was run.
