# Praxile P0 Engineering Checklist

This checklist tracks the current P0 implementation status from `Praxile_Next_Engineering_Web_Context_Roadmap.md`.

## P0-1 CLI Structure

- [x] Add shared `CommandContext` for CLI commands.
- [x] Route the new `praxile sync` command through service-layer logic.
- [x] Convert `praxile/cli.py` into a small compatibility entry point under 300 lines.
- [x] Move the legacy command implementation out of the public entry file.

Current boundary: command handlers still live in `praxile/cli_legacy.py` while they migrate behind services. The public `praxile.cli:main` target is now a small compatibility facade so package users and tests keep importing `praxile.cli`.

## P0-2 Store Structure

- [x] Split the public `praxile.store` entry into a small compatibility facade.
- [x] Add repository modules for tasks, trajectories, proposals, assets, retrieval, graph, index, feedback, and rollback.
- [x] Start extracting business operations above `ExperienceStore` into reusable services.
- [x] Keep `ExperienceStore` as the compatibility facade while internal repositories are introduced.

Current boundary: `ExperienceStore` remains the public facade used by runtime, CLI, Gateway, tests, and Reflect. The first split is compatibility-preserving: repository modules delegate to the existing store engine while deeper method-level migration continues behind the same API.

## P0-3 Service Layer

- [x] Add `RepositoryContextService`.
- [x] Add `ProposalService` for proposal list/detail/edit/accept/reject workflows.
- [x] Add `AssetService` for asset list/detail/usage/graph/lifecycle workflows.
- [x] Add service wrappers for run, graph, audit, reflect, spec, and model workflows.

## P0 Web Console MVP

- [x] Chat Workspace.
- [x] Model Roles configuration.
- [x] Run detail.
- [x] Proposal inbox/detail with edit/accept/reject.
- [x] Asset detail lite with usage, graph, archive/deprecate/reactivate.
- [x] Repository Context panel MVP.

## P0 Repository Context Panel

- [x] Last sync / freshness state.
- [x] Detected stacks.
- [x] Detected test commands.
- [x] Spec files and spec quality.
- [x] Docs files.
- [x] Git status and recent commits.
- [x] Recent failures.
- [x] Active assets.
- [x] Pending proposals.
- [x] Reflect report summary.
- [x] Context Health and ContextJuice summary.
