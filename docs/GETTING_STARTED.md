# Getting Started

This guide is the first-run path for Praxile as a standalone local self-evolving agent harness.

## 1. Install

Prerequisite: Python 3.11 or newer.

For a published release:

```bash
pipx install praxile
```

or:

```bash
uv tool install praxile
```

For local development:

```bash
git clone https://github.com/Praxile-Alpha/Praxile.git
cd Praxile
python -m pip install -e .
python -m pip install -e ".[http]"  # optional httpx transport
python -m pip install -e ".[vector]"  # optional sentence-transformers retrieval
python -m pip install -e ".[browser]"  # optional Playwright screenshots
python -m playwright install chromium
```

To preview the self-evolution loop before configuring a model endpoint, run:

```bash
praxile demo --fast --accept-first
```

The demo is local and deterministic: it creates a tiny Python project, captures or simulates a failing verification signal, fixes the bug, records the trajectory/reward/proposals, and accepts one low-risk memory proposal only inside that demo project. Use `--fast` for the no-subprocess preview; omit it when you want the demo to run the local unittest command.

## 2. Initialize And Configure

Praxile starts clean: no model provider is configured by package defaults. Initialize the repository, then run the setup wizard:

```bash
cd my-code-project
praxile init
praxile setup
praxile doctor
praxile doctor --online
praxile index status
```

`praxile setup` asks for the provider type, base URL, model name, and API-key environment variable name. It never asks for or stores raw API keys.

Non-interactive local Ollama example:

```bash
praxile setup \
  --provider ollama \
  --base-url http://localhost:11434/v1 \
  --model qwen2.5-coder:7b \
  --api-key-env OLLAMA_API_KEY \
  --channel none
```

Non-interactive OpenAI-compatible cloud example:

```bash
praxile setup \
  --provider openai-compatible \
  --base-url https://api.openai.com/v1 \
  --model <your-model> \
  --api-key-env OPENAI_API_KEY \
  --channel none
```

This sends a tiny chat request to each unique configured route target and fails fast when an API key, endpoint, or model is unavailable. Offline `praxile doctor` remains network-free.

## 3. State Layout

This creates:

```text
.praxile/
  config.json
  memory/
  skills/
  evals/
  rules/
  experience/
  backups/
  db/
  logs/
```

## 4. Run From CLI

```bash
praxile run "Fix the failing parser test" --test-command "python -m pytest"
praxile review --interactive
praxile explain latest
```

Every run creates:

- a trajectory;
- a reward report;
- zero or more experience proposals;
- auditable local state in `.praxile/`.

The run summary shows which accepted project experience was loaded and which proposals were produced. Use `praxile explain latest` to see why each memory, skill, rule, or failure pattern was loaded and what would become reusable after proposal approval.

Use `praxile review --pending` to list the proposal queue, or `praxile review --interactive` to process proposals one by one without copying IDs between commands. For larger queues:

```bash
praxile review --summary
praxile review --risk high
praxile review --type failure_pattern
praxile review --confidence high
praxile review --source-run <RUN_ID>
praxile review --high-risk
praxile review --recommended accept
praxile review --recommended reject_or_edit
praxile accept --all-low-risk
praxile accept --all-low-risk --yes
praxile reject --low-confidence --reason "too generic"
```

Interactive review explains each proposal with a recommended action, the reason, what future runtime behavior or retrieval it will affect, duplicate warnings, and a rollback command. `accept` means low-risk and well-evidenced; `inspect` is for architecture/routing/high-risk changes; `reject_or_edit` is for weak or generic proposals; `inspect_duplicate` means an active overlapping asset may already exist.

`praxile accept --all-low-risk` is a dry run by default. Add `--yes` to apply after reviewing the preview. Architecture gates, frozen boundaries, harness rules, and routing-policy proposals require explicit single-proposal review and are not batch-accepted by `--all-low-risk`.

Use `--dry-run` when you want Praxile to analyze, retrieve context, and record a trajectory without modifying files or running shell commands. Use `praxile index status` to inspect experience index health and `praxile index rebuild` if the SQLite/FTS index becomes stale after manual `.praxile/` edits.

Use `praxile models --stats` to summarize route performance from trajectories. Use `praxile consolidate` when accepted memories, skills, evals, or failure patterns start to overlap; it creates proposal-only governance updates such as `asset_merge` and `asset_deprecate`, plus cleanup review notes when needed. It does not delete or rewrite assets automatically. `praxile consolidate --all --summary` reports duplicate, stale, conflicting, and low-value governance counts without creating a proposal.

After reviewing a run, record human feedback so the next reward report and future retrieval can learn from it:

```bash
praxile feedback latest --positive "干得好"
praxile feedback latest --negative "这次方向错了"
praxile feedback prop_123 --negative "这个 proposal 太泛了"
praxile feedback asset .praxile/skills/test-repair/SKILL.md --helpful
```

Run feedback updates `user_feedback_reward` and `final_reward`. Proposal feedback influences review recommendations. Harmful asset feedback is governed: Praxile records the negative outcome and creates a proposal for lifecycle review instead of silently rewriting accepted memory or skills.

Accepted assets are `active` by default. Deprecated, superseded, and archived assets stay auditable but are excluded from normal retrieval. Use `praxile memory list --include-inactive`, `praxile asset status <PATH>`, `praxile asset deprecate <PATH>`, and `praxile asset archive <PATH>` to inspect or adjust lifecycle metadata manually.

For UI work, enable the optional browser adapter in `.praxile/config.json` and keep `browser.allowed_hosts` limited to local/dev hosts. `browser_screenshot` creates review evidence under `.praxile/experience/artifacts/browser/`.

## 5. Use Praxile Terminal

Start the interactive terminal:

```bash
praxile terminal
```

Common commands:

```text
status
run Fix the failing parser test --test-command "python -m pytest"
review
proposals
consolidate
accept <PROPOSAL_ID>
history
memory parser
channels
exit
```

The terminal is not a raw unrestricted shell. It is a Praxile command surface that routes tasks through the same safety, trajectory, reward, proposal, and rollback model as the normal CLI.

For scripts:

```bash
praxile terminal --command "status" --command "history 5"
```

Inside the terminal, `proposals` lists pending proposals before you run `review <PROPOSAL_ID>`, `accept <PROPOSAL_ID>`, or `reject <PROPOSAL_ID>`.

## 6. Try Cross-Stack Examples

The repository includes minimal examples for React, Go, and Rust:

```bash
cd examples/go
praxile init --force
praxile doctor
praxile run "Fix the greeting test" --test-command "go test ./..."
praxile review --interactive
```

These examples are deliberately small so the generated trajectory, reward report, and experience proposals are easy to inspect.

## 7. Use The Local Web Console

Start the gateway:

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

The console can:

- submit tasks;
- show recent history;
- review the latest task or proposal;
- accept proposals;
- list and bind Telegram/Discord channels.

The web console uses the same local gateway API. It does not bypass approval or write directly to memory/skills.

The browser console is intended for trusted localhost use. Praxile refuses non-localhost gateway binds such as `0.0.0.0` unless `--token` is provided. `--token` is best for API clients that can send `Authorization` or `X-Praxile-Token` headers.

## 7. Bind Channels

Telegram:

```bash
praxile channel bind telegram -1001234567890 \
  --name team-alerts \
  --mode bidirectional \
  --token-env TELEGRAM_BOT_TOKEN \
  --free-response
```

Discord:

```bash
praxile channel bind discord 123456789012345678 \
  --guild-id 987654321098765432 \
  --name dev-room \
  --mode task \
  --token-env DISCORD_BOT_TOKEN \
  --auto-thread
```

Inspect:

```bash
praxile channel list
praxile channel env
```

Current boundary: Praxile manages local channel configuration and gateway route metadata. Production Telegram/Discord bot listeners are a separate listener layer on top of this config.

## 8. Edit Config Manually

You can edit:

```text
.praxile/config.json
```

The file is JSONC-compatible JSON, not YAML. You may use comments and trailing commas while hand-editing; Praxile rewrites strict JSON when it saves config.

Good:

```jsonc
{
  // Keep raw secrets in the environment, not in config.
  "model_providers": {
    "openai_compatible": {
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "OPENAI_API_KEY",
    }
  }
}
```

Avoid:

```json
{
  "api_key": "raw-secret-token"
}
```

After editing:

```bash
praxile doctor
```

## 9. First Useful Loop

```bash
praxile run "Record the project test command and coding conventions" --max-steps 1
praxile review
praxile accept <PROPOSAL_ID>
praxile run "Fix a small bug using the accepted project memory"
```

That is the intended Praxile loop: every task can become reusable project experience, but durable updates still require user approval.
