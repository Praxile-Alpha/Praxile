# Getting Started

Praxile is installed as a CLI, initialized inside a code repository, and then used through CLI, terminal, or the local web console.

## Install

Prerequisite: Python 3.11 or newer.

```bash
pipx install praxile
# or
uv tool install praxile
```

For local development:

```bash
python -m pip install -e .
python -m pip install -e ".[http]"  # optional httpx transport
python -m pip install -e ".[vector]"  # optional sentence-transformers retrieval
python -m pip install -e ".[browser]"  # optional Playwright screenshots
python -m playwright install chromium
```

## Initialize And Configure

```bash
cd my-code-project
praxile init
praxile setup
praxile doctor
praxile doctor --online
praxile index status
```

Fresh installs start with no provider configured. `praxile setup` writes project-local model roles into `.praxile/config.json` and stores only environment variable names, not raw API keys.

```bash
praxile setup --provider ollama --model qwen2.5-coder:7b --channel none
praxile setup --provider openai-compatible --base-url https://api.openai.com/v1 --model <your-model> --api-key-env OPENAI_API_KEY --channel none
```

`praxile init` detects Python, Node/React, Go, and Rust markers and seeds verification commands for new projects. `praxile review --pending` shows the proposal queue, and `praxile review --interactive` lets you process proposals in one pass.

## Run

```bash
praxile demo --fast --accept-first
praxile run "Fix the failing parser test" --test-command "python -m pytest"
praxile review --interactive
praxile explain latest
praxile review --summary
praxile review --source-run <RUN_ID>
praxile review --high-risk
praxile review --recommended accept
praxile review --recommended reject_or_edit
praxile accept <PROPOSAL_ID>
```

The run summary shows which accepted project experience was loaded and which proposals were produced. `praxile explain latest` shows why each memory, skill, rule, or failure pattern was loaded and what would become reusable after proposal approval.

`praxile review --summary`, `--risk`, `--type`, `--confidence`, `--source-run`, `--high-risk`, and `--recommended` keep proposal review focused. Interactive review explains what each proposal means, the recommended action, why, what future runtime behavior or retrieval it will affect, duplicate warnings, and a rollback command. `accept` means low-risk and well-evidenced; `inspect` is for architecture/routing/high-risk changes; `reject_or_edit` is for weak or generic proposals; `inspect_duplicate` means an active overlapping asset may already exist. `praxile accept --all-low-risk` previews a low-risk batch and `praxile accept --all-low-risk --yes` applies only low-risk memory/eval-style proposals; architecture gates, frozen boundaries, harness rules, and routing-policy proposals still require explicit single-proposal review. `praxile reject --low-confidence --reason "too generic"` records feedback for low-evidence proposals.

`--dry-run` records analysis and planning while blocking file edits and shell commands. `praxile index status` shows SQLite/FTS index health; use `praxile index rebuild` only when manual `.praxile/` edits leave the index stale.

`praxile demo --fast --accept-first` runs a local deterministic no-model preview of the self-evolution loop in a disposable project. Omit `--fast` to run the local unittest command. `praxile models --stats` summarizes route performance from trajectories. `praxile consolidate` scans overlapping experience assets and creates proposal-only governance updates such as `asset_merge` and `asset_deprecate`, plus cleanup review notes when needed. `praxile consolidate --all --summary` reports duplicate, stale, conflicting, and low-value governance counts.

Record human feedback after review so the reward loop learns from it:

```bash
praxile feedback latest --positive "干得好"
praxile feedback latest --negative "这次方向错了"
praxile feedback prop_123 --negative "这个 proposal 太泛了"
praxile feedback asset .praxile/skills/test-repair/SKILL.md --helpful
```

Run feedback updates `user_feedback_reward` and `final_reward`. Proposal feedback changes review recommendations. Harmful asset feedback records a negative outcome and creates a proposal-only lifecycle review instead of silently rewriting accepted assets.

Accepted assets are `active` by default. Deprecated, superseded, and archived assets stay auditable but are excluded from normal retrieval. Use `praxile memory list --include-inactive`, `praxile asset status <PATH>`, `praxile asset deprecate <PATH>`, and `praxile asset archive <PATH>` to inspect or adjust lifecycle metadata manually.

For UI work, enable the optional browser adapter in `.praxile/config.json` and keep `browser.allowed_hosts` limited to local/dev hosts. `browser_screenshot` creates review evidence under `.praxile/experience/artifacts/browser/`.

## Terminal

```bash
praxile terminal
```

Useful terminal commands:

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

## Web Console

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

The browser console is intended for trusted localhost use. Praxile refuses non-localhost gateway binds such as `0.0.0.0` unless `--token` is provided. `--token` is best for API clients that can send `Authorization` or `X-Praxile-Token` headers.

## Config

Edit `.praxile/config.json` directly when needed. It is JSONC-compatible JSON for hand editing, and it should store environment variable names rather than raw secrets.
