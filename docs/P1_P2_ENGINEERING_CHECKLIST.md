# P1/P2 Engineering Checklist

This checklist tracks the Web Context roadmap work after the P0 structure pass.

## P1

- [x] Context Health exposes sync freshness, repository coverage, asset/index/graph health, and ContextJuice signals.
- [x] `praxile sync` writes `.praxile/context/repo_snapshot.json` in addition to historical snapshots.
- [x] `praxile sync --since/--docs/--specs/--ci/--github/--watch` supports scoped, repeatable repository context collection.
- [x] `praxile sync` writes separate recent commit and working-tree diff context files under `.praxile/context/commits/` and `.praxile/context/diffs/`.
- [x] `praxile sync --github-online` can opt into GitHub PR/issue summary fetches without making remote network calls the default.
- [x] ContextJuice can compress a source file, latest run, or raw text with preserved evidence metadata.
- [x] ContextJuice chooses format-aware strategies for diffs, test logs, Markdown/HTML, and generic tool output.
- [x] ContextJuice supports role-specific compression profiles through `.praxile/policies/context.json`.
- [x] Web Console can show ContextJuice status and trigger safe compression.

## P2

- [x] Repository Memory Tree writes human-readable markdown under `.praxile/context/tree/`.
- [x] Policy Layers can list, check, and explain default/user/project/context/tool/proposal policies.
- [x] `.praxile/policies/tool_policy.json` can deny matching runtime tool calls through `SafetyPolicy`.
- [x] Runtime context compression now uses the same role profiles as ContextJuice and records compression evidence in trajectories.
- [x] Background Governance Loop can run one safe pass: sync, optional compress, optional graph rebuild, optional audit, optional reflect.
- [x] `praxile watch --iterations N` can run bounded repeated safe governance passes.
- [x] Governance Loop never accepts proposals, rewrites durable assets, or edits code.
- [x] Workflow templates can be listed, shown, seeded, customized, and inspected through CLI/API/Web Console.
- [x] Web Console can inspect Memory Tree, Workflow Templates, Policy Layers, and governance reports.
