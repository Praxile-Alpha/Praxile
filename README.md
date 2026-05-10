# Praxile

**Governed experience harness for AI coding**

Praxile captures what an AI coding agent actually did, turns that run into evidence-backed proposals, and stores only approved repository-local experience under `.praxile/`.

It is not a general-purpose coding agent, not a hidden global memory, and not a Spec Kit replacement. Praxile is the governance layer around coding work: environment interaction, trajectory logging, reward, experience extraction, proposal review, audit, rollback, and future retrieval.

[中文 README](README.zh-CN.md)
<img width="1254" height="1254" alt="praxile" src="https://github.com/user-attachments/assets/63f4566c-3ce1-4eed-82fe-f87fd93141ba" />


Project: [https://github.com/Praxile-Alpha/Praxile](https://github.com/Praxile-Alpha/Praxile)

## Why Praxile?

Most coding agents can edit files and run tests. The harder problem is deciding what the project should remember after the run.

Praxile makes that memory loop explicit:

```text
user task
  -> environment interaction
  -> trajectory
  -> reward report
  -> evidence and episodes
  -> experience proposals
  -> human review
  -> approved memory / skill / rule / eval / boundary
  -> better future retrieval
```

This is the core promise: every durable lesson must be scoped, evidenced, reviewable, auditable, and reversible.

## What Praxile Adds

- **Repository-local experience**: memories, skills, rules, evals, failure patterns, project patterns, frozen boundaries, and architecture gates live under `.praxile/`.
- **Proposal-governed evolution**: durable updates start as proposals. They are not silently written into active memory.
- **Spec-aware context**: optional `spec.md`, `.specify/`, plan, task, and constitution context can shape reward and proposal gating.
- **Reward reports**: Praxile separates task success, regression safety, process safety, cost, experience value, and human feedback.
- **Experience graph**: explain why an asset was loaded, where a proposal came from, and which runs or specs it affected.
- **Workspace isolation**: run tasks in-place or in isolated per-task workspaces, including Git worktree mode.
- **Audit and CI gates**: export run, proposal, asset, and project-level audit chains with default redaction.
- **Safety controls**: sensitive path protection, dangerous command blocking, diff review, backups, rollback, architecture gates, and interop guardrails.
- **Optional terminal and gateway**: use the normal CLI, an interactive Praxile terminal, or a local web console.

## Install

Praxile requires Python 3.11 or newer.

Recommended one-line install from GitHub:

```bash
pipx install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

Alternative with `uv`:

```bash
uv tool install "git+https://github.com/Praxile-Alpha/Praxile.git"
```

Development install:

```bash
git clone https://github.com/Praxile-Alpha/Praxile.git
cd Praxile
python -m pip install -e ".[http]"
```

Optional extras:

```bash
python -m pip install -e ".[vector]"   # sentence-transformers semantic retrieval
python -m pip install -e ".[browser]"  # Playwright browser evidence
python -m playwright install chromium
```

You can also review and run the installer script from the repository:

```bash
curl -fsSLO https://raw.githubusercontent.com/Praxile-Alpha/Praxile/main/install.sh
sh install.sh
```

## Try It Without A Model

The demo runs locally and does not require a model endpoint:

```bash
praxile demo --fast --accept-first --show-files
```

It creates a tiny project, records a trajectory, builds a reward report, generates proposals, accepts one low-risk memory inside the demo project, and shows how the next run would retrieve it.

## Quick Start In A Code Repository

```bash
cd /path/to/your/code-project
praxile init
praxile setup
praxile doctor
praxile doctor --online
```

`praxile setup` configures providers and model roles step by step. Praxile does not ship with preconfigured cloud credentials and does not store raw API keys. It stores environment variable names such as `OPENAI_API_KEY` or `OLLAMA_API_KEY`.

Run a task:

```bash
praxile run "Fix the failing parser test" --test-command "python -m pytest"
```

Review what Praxile learned:

```bash
praxile review --interactive
praxile accept <PROPOSAL_ID>
praxile explain latest
```

## Model Configuration

Praxile starts clean: `model_providers` is empty until the user configures it.

Minimum useful role:

- `coding_agent`: required for autonomous code-editing runs.

Recommended self-evolution roles:

- `evidence_extraction`
- `experience_reflection`
- `proposal_composer`
- `review_recommendation`

Optional semantic judge roles:

- `reward_judge`
- `feedback_classifier`
- `attribution_judge`
- `counterexample_checker`
- `pattern_mining`
- `project_pattern_composer`
- `deep_project_pattern_mining`

Common local setup:

```bash
praxile setup \
  --provider ollama \
  --base-url http://localhost:11434/v1 \
  --model qwen2.5-coder:7b \
  --api-key-env OLLAMA_API_KEY \
  --channel none
```

Common OpenAI-compatible setup:

```bash
praxile setup \
  --provider openai-compatible \
  --base-url https://api.openai.com/v1 \
  --model <your-model> \
  --api-key-env OPENAI_API_KEY \
  --channel none
```

See [praxile.config.example.json](praxile.config.example.json) and [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for role routing, fallback models, local-first policies, semantic judges, retrieval, reward weights, gateway, and channel settings.

## Spec-Aware Workflow

Attach spec context when a task has explicit intent, non-goals, acceptance criteria, or success metrics:

```bash
praxile run "Implement search API" \
  --spec docs/specs/search.md \
  --test-command "python -m pytest"

praxile spec verify latest
```

Spec compliance influences reward and proposal quality. A task can pass tests but still produce weak or blocked experience proposals if it violates scope, skips acceptance criteria, or changes architecture without a gate.

## Experience Assets

Praxile experience is not only Markdown and not only a graph.

- Human-readable durable assets are Markdown or JSON under `.praxile/`.
- SQLite indexes support retrieval, search, usage tracking, lifecycle status, and graph queries.
- The experience graph is explanatory infrastructure. It can be rebuilt from trajectories, proposals, specs, and assets.
- Approved assets are active by default. Deprecated, superseded, and archived assets stay auditable but are excluded from normal retrieval.

Useful commands:

```bash
praxile memory list --include-inactive
praxile skill list
praxile asset status .praxile/memory/project.md
praxile graph status --rebuild
praxile graph explain .praxile/memory/project.md
praxile graph trace <PROPOSAL_ID>
```

## Audit And Governance

Audit commands are read-only exports:

```bash
praxile audit run latest --json
praxile audit proposal <PROPOSAL_ID> --json
praxile audit asset .praxile/memory/project.md --json
praxile audit bundle --redaction strict --output praxile-governance-bundle.json
praxile audit check --strict --rebuild-graph --redaction strict
```

Redaction modes:

- `standard`: default, masks likely secret values while preserving lineage.
- `strict`: also removes raw content, observation, output, and diff excerpts.
- `none`: local debugging only.

`audit check` is CI-friendly. It fails when required governance conditions are not met, such as incomplete constitution, pending high-risk proposals, missing graph evidence in strict mode, or a failed latest run when configured.

## Terminal, Gateway, And Channels

Interactive terminal:

```bash
praxile terminal
```

Local web console:

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

Channel configuration:

```bash
praxile channel bind telegram -1001234567890 --name team-alerts --token-env TELEGRAM_BOT_TOKEN
praxile channel bind discord 123456789012345678 --name dev-room --token-env DISCORD_BOT_TOKEN
praxile channel list
```

Current boundary: Praxile manages local channel bindings and gateway route metadata. Production Telegram or Discord bot listeners are a separate listener layer on top of this config.

## Common Commands

```text
praxile init                 Initialize .praxile in the current repository
praxile setup                Configure providers, model roles, and optional channels
praxile demo --fast          Run a local governed-experience demo
praxile run "..."            Execute an agent task
praxile run "..." --dry-run  Analyze and record without editing files
praxile run "..." --workspace-mode copy
                             Run in an isolated per-task workspace
praxile review --interactive Review pending proposals
praxile accept <PROPOSAL_ID> Accept one pending proposal
praxile reject <PROPOSAL_ID> Reject one pending proposal
praxile history              List trajectory history
praxile explain latest       Explain retrieval, reward, and proposals
praxile spec check           Check optional spec quality signals
praxile spec verify latest   Verify a completed run against spec context
praxile constitution check   Check experience-governance principles
praxile graph status         Show experience graph status
praxile audit check          Run a CI-friendly governance gate
praxile consolidate --all    Propose cleanup for stale or overlapping assets
praxile models --stats       Show provider routes and observed model performance
praxile tools                List supported tool actions
praxile rollback             Roll back task edits or accepted proposals
praxile terminal             Start the interactive Praxile terminal
praxile gateway serve        Start the local web console/API
praxile doctor --online      Validate config, routes, and local state
```

## Local State

Praxile writes repository-local state under `.praxile/`:

```text
.praxile/
  config.json
  constitution.md
  memory/
  skills/
  evals/
  rules/
  experience/
  backups/
  db/
  logs/
```

Do not put raw secrets in `.praxile/config.json`. Use environment variables through `api_key_env` and channel `token_env` settings.

## Interop Boundary

Praxile can detect optional Hermes/OpenClaw capabilities and can use OpenAI-compatible endpoints, but it is not a Hermes or OpenClaw plugin.

- `.praxile/memory` is not written into external global memory.
- `.praxile/skills` are not installed into external skill stores.
- Praxile trajectories are the source of truth; external-compatible sidecars are exports.
- Future external sync should go through explicit adapter commands and auditable proposals.

## Documentation

- [Getting Started](docs/GETTING_STARTED.md)
- [Configuration](docs/CONFIGURATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Experience Model](docs/EXPERIENCE_MODEL.md)
- [Audit Governance](docs/audit-governance.md)
- [Why Praxile](docs/WHY_PRAXILE.md)
- [Install And Interop](docs/INSTALL_AND_INTEROP.md)
- [Testing Guide](docs/contributing-testing.md)
- [Security Policy](SECURITY.md)

## Current Status

Praxile is Alpha software. The core local loop is implemented: init, setup, run, trajectory, reward, proposal generation, review, accept/reject, retrieval, graph, audit, rollback, terminal, gateway, and channel configuration.

Not included in the first release:

- automatic model weight training;
- marketplace distribution;
- silent global memory sync;
- automatic production Telegram/Discord listeners;
- unrestricted shell execution;
- autonomous acceptance of durable experience.

## License

MIT
