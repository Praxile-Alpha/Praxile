# Praxile Reflect

Praxile Reflect is offline, proposal-governed experience refinement.

It analyzes accumulated repository-local experience and produces reviewable findings and proposals for cleanup, consolidation, deprecation, and pattern refinement.

Reflect does not rewrite memory directly.

## Inputs

Reflect reads the local `.praxile/` experience system:

- trajectories and reward reports;
- accepted, pending, and rejected proposals;
- memory, skills, rules, evals, failure patterns, and project patterns;
- asset usage and attribution counters;
- user feedback;
- silent-failure signals;
- experience graph status.

## Commands

```bash
praxile reflect
praxile reflect --summary
praxile reflect --since 7d
praxile reflect --asset .praxile/memory/project.md
praxile reflect --duplicates --stale --silent-failures
praxile reflect --harmful --rejected-proposals --high-value-patterns
praxile reflect --report json
praxile reflect --report markdown --output reflect.md
praxile reflect --write-proposals
praxile reflect --ci
```

Default mode runs the P0 governance analyzers: duplicate assets, stale assets, and repeated silent failures. Use flags to select specific analyzers.

## Findings

Reflect can emit:

- `duplicate_asset`
- `stale_asset`
- `harmful_asset`
- `silent_failure_pattern`
- `rejected_proposal_theme`
- `high_value_pattern`

Each finding includes confidence, severity, affected assets/runs/proposals, evidence references, reason, and a recommended action.

## Proposals

With `--write-proposals`, Reflect writes only pending proposals after the proposal gate passes them. Typical proposal types:

- `asset_merge`
- `asset_deprecate`
- `harness_rule_create`
- `proposal_gate_policy_update`
- `pattern_promote`

Reflect proposals include:

- `generated_by: reflect`
- `reflect_id`
- `finding_id`
- affected assets/runs/proposals
- normal evidence, scope, anti-scope, confidence, risk, and rollback fields

## Reports

Use JSON for automation:

```bash
praxile reflect --report json --output reflect.json
```

Use Markdown for human review:

```bash
praxile reflect --report markdown --output reflect.md
```

When `--write-proposals` is used, Praxile also stores a source report under:

```text
.praxile/experience/reflect/reflect_<id>.json
```

Reflect reports can be included in project-level release evidence:

```bash
praxile audit bundle --include-reflect --redaction strict --output bundle.json
```

## CI Mode

`praxile reflect --ci` is designed for scheduled jobs and release checks. It differs from the normal command in three ways:

- it runs all Reflect analyzers by default;
- it writes JSON and Markdown artifacts under `reflect.ci.artifact_dir`;
- it returns a policy exit code based on configured thresholds.

Common examples:

```bash
praxile reflect --ci
praxile reflect --ci --since 7d --max-high-severity 0
praxile reflect --ci --max-findings 20 --ci-output-dir .praxile/experience/reflect/ci
```

CI thresholds are configured under `reflect.ci`:

```json
{
  "reflect": {
    "ci": {
      "default_since": "7d",
      "artifact_dir": ".praxile/experience/reflect/ci",
      "max_findings": null,
      "max_high_severity": 0,
      "max_generated_proposals": null,
      "write_github_step_summary": true
    }
  }
}
```

The command still does not modify active assets. Use `--write-proposals` explicitly if a scheduled governance job should create pending proposals for later human review.

A copyable GitHub Actions template is available at:

```text
examples/github-actions/praxile-reflect.yml
```

## Boundary

Reflect is not an automatic memory cleaner. It is a governance pass over the experience system.

```text
No reflective update without proposal.
No proposal without evidence.
No durable change without human review.
```
