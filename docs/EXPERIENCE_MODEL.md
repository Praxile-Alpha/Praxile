# Praxile Experience Model

Praxile is **Markdown-first** for human review, **JSON-backed** for structured run records, **SQLite-indexed** for fast retrieval, and **Proposal-governed** for durable memory.

## 1. Storage Layers

| Layer | Purpose |
|---|---|
| **Markdown** | Human review, Git diffs, accepted memory, skills, rules, boundaries, and checklists. |
| **JSON** | Trajectories, evidence, episodes, reward reports, proposals, feedback, and checkpoints. |
| **SQLite** | Asset metadata, lifecycle, usage, proposal index, trajectory index, FTS, retrieval ranking, and the rebuildable Experience Graph. |
| **FTS** | Local keyword retrieval over approved experience assets. |
| **Vector** | Optional similarity retrieval; `local_hash` is the lightweight fallback, `sentence_transformers` is for semantic search. |

## 2. Experience Lifecycle

The core learning loop turns ephemeral execution into durable, governed assets:

```text
Task Execution -> Trajectory -> Evidence -> Episode -> Proposal -> Review -> Accepted Asset -> Future Retrieval
```

Every durable asset carries (or implies):
- **Source Task**: The original trajectory that spawned the lesson.
- **Evidence Summary**: Concrete facts that justify the lesson.
- **Confidence**: How certain the agent is about the rule.
- **Applicability Scope**: When this rule should trigger.
- **Anti-Scope**: When this rule should *not* trigger.
- **Target Files / Rollback Path**: The exact changes to `.praxile/`.
- **Lifecycle Status**: Active, Deprecated, Superseded, or Archived.

The **Proposal Gate** automatically suppresses weak candidates (e.g., low confidence, no evidence) before they reach the pending inbox, keeping the review process noise-free.

## 3. Experience Graph

Praxile builds a local SQLite relationship index from Markdown and JSON files. The graph is rebuildable, project-local, and acts as the relational backbone for governance.

**Nodes include:**
- Runs, Proposals, Approved Assets, Attached Specs, and Recorded Executors.

**Edges include:**
- `derived_from_spec`, `generated_from_run`, `supports_proposal`, `contradicts_asset`, `participated_in_run`, `approved_by`, `retrieved_in_run`, `helped_run`, `misled_run`, `supersedes`, `deprecated_by`, `violates_spec`, `satisfies_spec`.

**Useful commands:**
```bash
praxile graph status --rebuild
praxile graph explain .praxile/memory/project.md --depth 2
praxile graph trace prop_abc123
praxile graph impact docs/specs/feature.md
```

The graph answers governance questions such as:
- *"Why was this memory loaded?"*
- *"Which run generated this proposal?"*
- *"Which accepted asset may be misleading future runs?"*

## 4. Spec Compliance & Attribution

**Spec Compliance:**
Spec context anchors the run. After execution, `praxile spec verify latest` compares the trajectory, diff, actions, and reward against attached spec files. It checks acceptance criteria, non-goals, and constraints, writing a `spec_compliance` report back to the trajectory. This influences scoring and proposal gating.

**Attribution Levels:**
Retrieval attribution tracks the actual impact of loaded assets:
- `loaded_only`: Retrieved and placed in context.
- `referenced`: Referenced by the agent, but outcome unclear.
- `strong_positive`: Explicitly used in a successful run.
- `harmful`: Explicitly involved in a bad outcome or marked harmful by human feedback.
