# Praxile Agent Directory

This directory is the Praxile standalone local self-evolving agent harness.

It intentionally contains more than Python modules:

- `*.py`: runtime, environments, reward, store, safety, CLI, and optional adapter detection.
- `templates/`: seed assets copied into `.praxile/` by `praxile init`.
- `examples/`: concrete proposal and trajectory shapes for users and contributors.
- `docs/`: product, install, interop, configuration, and architecture notes for the agent itself.

Praxile keeps project-local memory, skills, evals, rules, proposals, rewards, and rollback state in the target repository's `.praxile/` directory. It can run directly against configured local/cloud model endpoints. Optional Hermes/OpenClaw adapters must not own Praxile state or receive project memory/skills automatically.

Channel bindings for Telegram/Discord live in `.praxile/config.json` and store environment variable names such as `TELEGRAM_BOT_TOKEN`, not raw tokens.

Interactive entrypoints:

- `praxile terminal` for the local agent terminal;
- `praxile gateway serve` then `http://127.0.0.1:8765/` for the local web console.

Alpha usability helpers:

- `praxile demo --accept-first` runs a local no-model demo of the self-evolution loop;
- `praxile init` detects Python, Node/React, Go, and Rust project markers;
- `praxile doctor` reports configured/suggested verification commands, missing tools, index health, and project-map coverage;
- `praxile run --dry-run` records analysis/planning trajectories while blocking edits and shell commands;
- `praxile index status` and `praxile index rebuild` maintain the local SQLite/FTS experience index;
- `praxile explain latest` shows which accepted experience was loaded and what the run produced;
- `praxile feedback latest --positive/--negative` and `praxile feedback asset ... --helpful/--harmful` feed human judgement into hybrid reward and governed asset outcomes;
- `praxile review --summary`, filters, and `praxile review --interactive` make proposal review less copy-heavy;
- `praxile accept --all-low-risk` and `praxile reject --low-confidence` support governed batch handling while keeping high-risk proposals manual.
- `praxile consolidate --all` generates proposal-only duplicate/stale/conflict/low-value experience governance notes.
