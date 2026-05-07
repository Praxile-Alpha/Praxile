# State Layout

`praxile init` creates `.praxile/` in the target code repository.

```text
.praxile/
  config.json
  memory/
    user.md
    project.md
    shards/
    decisions.md
    failures.md
  skills/
    <skill-name>/
      SKILL.md
      metadata.json
      versions/
  experience/
    trajectories/
    failures/
    artifacts/
    proposals/
      pending/
      accepted/
      rejected/
  evals/
    checklists/
    regression-cases/
  rules/
    frozen-boundaries/
    architecture-gates/
    harness-rules/
  backups/
  checkpoints/
  db/
  logs/
```

The `.praxile/` directory is project-local. Accepted assets from one repository should not be treated as global user memory or automatically loaded by an external framework.

Experience assets are lifecycle-managed. Praxile loads only `active` assets during normal retrieval; `deprecated`, `superseded`, and `archived` assets stay auditable on disk and in the index but are excluded by default. Lifecycle metadata is stored as sidecar `*.meta.json` files, so governance proposals can retire or supersede an asset without rewriting the original learning note. Use `praxile memory list --include-inactive` and `praxile asset status <PATH>` when auditing retired assets.

Skills additionally keep `metadata.json` for status/version and `versions/` for accepted snapshots, so rollbacks and history remain clear.

`experience/artifacts/` stores runtime evidence such as optional browser screenshots. These artifacts support review, but they do not replace human UX acceptance.

`checkpoints/` stores resumable in-flight task state and is cleared when a run finishes normally. `logs/trace.jsonl` stores structured runtime events for debugging model routing, safety blocks, context compression, and checkpoint writes.
