# State Layout

`praxile init` creates `.praxile/` in the target code repository.

```text
.praxile/
  config.json
  constitution.md
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
    chat/
      sessions/
    trajectories/
    failures/
    reflect/
    artifacts/
    proposals/
      pending/
      accepted/
      rejected/
  evals/
    checklists/
    regression-cases/
  rules/
    safety-policy.json
    frozen-boundaries/
    architecture-gates/
    harness-rules/
  backups/
  checkpoints/
  snapshots/
  db/
  logs/
  workspaces/
    <workspace-id>/
      metadata.json
      root/
```

The `.praxile/` directory is project-local. Accepted assets from one repository should not be treated as global user memory or automatically loaded by an external framework.

`constitution.md` is the repo-local experience constitution. It records the governance principles that keep durable memory, skills, rules, evals, and frozen boundaries evidence-backed and reviewable. `praxile constitution check` verifies the file exists and still contains the required principles.

Experience assets are lifecycle-managed. Praxile loads only `active` assets during normal retrieval; `deprecated`, `superseded`, and `archived` assets stay auditable on disk and in the index but are excluded by default. Lifecycle metadata is stored as sidecar `*.meta.json` files, so governance proposals can retire or supersede an asset without rewriting the original learning note. Use `praxile memory list --include-inactive` and `praxile asset status <PATH>` when auditing retired assets.

`rules/safety-policy.json` is a project-local deny-only tool policy. It is loaded by `SafetyPolicy` before runtime tool calls and by Gateway safety dry-run APIs, so rules in this file affect the next run immediately after reviewable human edits.

Skills additionally keep `metadata.json` for status/version and `versions/` for accepted snapshots, so rollbacks and history remain clear.

`experience/artifacts/` stores runtime evidence such as optional browser screenshots. These artifacts support review, but they do not replace human UX acceptance.

`experience/chat/sessions/` stores local Web Console chat sessions. A session links user/assistant messages to Praxile run IDs; trajectories remain the source of truth for actions, observations, rewards, diffs, and proposals.

`experience/reflect/` stores source reports for `praxile reflect --write-proposals`. Reflect reports explain offline governance findings and the pending proposals they generated; they are not active retrieval assets. CI mode stores JSON and Markdown artifacts under `experience/reflect/ci/` by default.

`workspaces/` stores optional per-task isolated workspaces created by `praxile run --workspace-mode copy` or `--workspace-mode git-worktree`. Isolated runs import their trajectory and proposals back into the source project and write patch artifacts under `experience/artifacts/workspaces/`; source project files are not changed automatically.

`snapshots/` stores point-in-time copies of governed `.praxile/` state. Generated indexes and caches are excluded because they can be rebuilt. Praxile creates a pre-apply snapshot before accepting a proposal, and users can create or restore snapshots with `praxile snapshot create`, `praxile snapshot list`, and `praxile rollback <SNAPSHOT_ID>`.

`checkpoints/` stores resumable in-flight task state and is cleared when a run finishes normally. `logs/trace.jsonl` stores structured runtime events for debugging model routing, safety blocks, context compression, and checkpoint writes.
