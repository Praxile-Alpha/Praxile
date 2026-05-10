# Audit Governance

Praxile audit commands are read-only governance surfaces over the local `.praxile/` source of truth. They do not accept proposals, sync memory, edit files, or grant Praxile new write authority.

## What To Export

```bash
praxile audit run latest --json
praxile audit proposal prop_abc123 --json
praxile audit asset .praxile/memory/project.md --json
praxile audit bundle --redaction strict --output praxile-governance-bundle.json
```

- `audit run` explains one task's task analysis, model route, actions, reward, loaded assets, and generated proposals.
- `audit proposal` explains evidence, review state, target files, and applied changes for one proposal.
- `audit asset` explains where a durable experience asset came from and how it has been used.
- `audit bundle` summarizes recent runs, pending proposals, asset lifecycle counts, graph status, and release-review recommendations.

## Redaction Modes

| Mode | Use | Behavior |
|---|---|---|
| `standard` | Default local/team review | Masks likely secret values while preserving excerpts and lineage. |
| `strict` | CI artifacts, release bundles, external review | Also removes raw content, observation, output, and diff excerpts. |
| `none` | Local debugging only | Preserves raw audit content and may include command output or secrets. |

Every audit report includes a `redaction` block with the selected profile and redaction counts.

## CI Gate

Use `audit check` as a release-time gate:

```bash
praxile audit check \
  --strict \
  --rebuild-graph \
  --redaction strict \
  --output praxile-audit-check.json
```

By default, the gate fails if the experience constitution is incomplete or if high-risk/p0 proposals are still pending. `--strict` also requires zero pending proposals, a built graph, and a clean latest run.

For less strict teams, allow low-risk pending proposals but keep high-risk review mandatory:

```bash
praxile audit check \
  --max-pending 10 \
  --max-high-risk-pending 0 \
  --redaction strict
```

## Recommended Release Flow

1. Run project verification commands.
2. Run `praxile spec verify latest` when the task had attached specs.
3. Review pending proposals with `praxile review --interactive`.
4. Rebuild relationship evidence with `praxile graph status --rebuild`.
5. Run `praxile audit check --strict --redaction strict`.
6. Archive `praxile audit bundle --redaction strict` as release evidence when needed.

