# Praxile Experience Model

Praxile is Markdown-first for review, JSON-backed for run records, SQLite-indexed for retrieval, and proposal-governed for durable memory.

## Storage Layers

| Layer | Purpose |
|---|---|
| Markdown | Human review, git diffs, accepted memory, skills, rules, boundaries, and checklists |
| JSON | Trajectories, evidence, episodes, reward reports, proposals, feedback, and checkpoints |
| SQLite | Asset metadata, lifecycle, usage, proposal index, trajectory index, FTS, retrieval ranking, and rebuildable experience graph |
| FTS | Local keyword retrieval over approved experience assets |
| Vector | Optional similarity retrieval; `local_hash` is lightweight fallback, `sentence_transformers` is semantic |

## Experience Lifecycle

```text
trajectory -> evidence -> episode -> proposal -> review -> accepted asset -> future retrieval -> outcome feedback
```

Every durable asset should carry or imply:

- source task or source episode
- evidence summary
- confidence
- applicability scope
- anti-scope
- target files or rollback path
- lifecycle status

The proposal gate suppresses weak candidates before they reach the pending inbox. Rejected proposals and harmful asset feedback remain part of the learning signal.

## Experience Graph MVP

Praxile keeps Markdown and JSON files as the source of truth, then builds a local SQLite relationship index from them. The graph is rebuildable and project-local; it is not a separate hidden memory store.

Nodes include:

- runs
- proposals
- approved assets
- attached specs
- recorded executors

Edges include:

- `derived_from_spec`
- `generated_from_run`
- `supports_proposal`
- `contradicts_asset`
- `participated_in_run`
- `approved_by`
- `retrieved_in_run`
- `helped_run`
- `misled_run`
- `supersedes`
- `deprecated_by`
- `violates_spec`
- `satisfies_spec`

Useful commands:

```bash
praxile graph status --rebuild
praxile graph explain .praxile/memory/project.md --depth 2
praxile graph trace prop_abc123
praxile graph impact specs/feature.md
praxile audit run latest --json
praxile audit asset .praxile/memory/project.md --json
praxile audit proposal prop_abc123 --output proposal-audit.json
praxile audit bundle --json
praxile audit check --strict --rebuild-graph
```

The graph exists to answer governance questions such as “why was this memory loaded?”, “which run generated this proposal?”, “which assets were created from this spec?”, and “which accepted asset may be misleading future runs?”.

Executor attribution is also part of the evidence chain. A trajectory records registered executors and per-action ownership; reward reports summarize attribution quality and parallel exploration issues; memory, failure-pattern, and harness-rule proposals can cite those facts. This keeps a failed read-only explorer, a primary coding decision, and a verification step from being collapsed into a single vague “agent did it” event.

Audit exports package the same chain for humans, CI archives, or external governance systems. They are read-only reports; accepting, rejecting, archiving, or rolling back experience still goes through the normal proposal and lifecycle commands. The project-level bundle is intentionally a summary, not a portable memory sync format; it can be archived as release evidence without making another Praxile project load these assets automatically. `audit check` turns that summary into a CI-friendly pass/fail gate for constitution completeness, pending high-risk proposals, graph readiness, and optional strict release policy.

Audit JSON defaults to `--redaction standard`, which masks likely secret values while preserving lineage structure. Use `--redaction strict` for shareable team or CI artifacts when raw content excerpts are unnecessary, and reserve `--redaction none` for local debugging.

## Spec Compliance

Spec context is not a replacement for tests or review. After a run, `praxile spec verify latest` compares the trajectory, diff, actions, and reward notes against attached spec files. It checks acceptance criteria, non-goals, constraints, and success-metric coverage, then writes a `spec_compliance` report back to the trajectory for later proposal review. Normal `praxile run --spec ...` finishes now perform the same check automatically before reward and evolution, so compliance gaps influence both scoring and proposal gating.

## Attribution Levels

Retrieval attribution is intentionally staged:

- `loaded_only`: the asset was retrieved and placed in context.
- `referenced`: the run appears to reference the asset but outcome is not yet clear.
- `weak_positive`: the asset was referenced in a successful run.
- `strong_positive`: the asset was explicitly used in a successful run or marked helpful.
- `neutral`: no useful positive or negative effect is known.
- `weak_negative`: the asset was referenced in a failed run.
- `harmful`: the asset was explicitly involved in a bad outcome or marked harmful.
