# Core Layers

Praxile is a full local agent harness, not just a self-evolution plugin.

## Provider

`praxile.model` owns provider clients and routing:

- OpenAI-compatible chat completions;
- Anthropic messages;
- local endpoints such as Ollama-compatible servers;
- purpose-based routes for planning, coding, private work, cheap summarization, and evolution.

## Runtime

`praxile.runtime.AgentRuntime` owns the task loop:

- analyzes privacy and architecture risk;
- retrieves project memory, skills, evals, and rules;
- routes the model call;
- executes JSON actions through the tool registry;
- records every action in a trajectory;
- builds reward and experience proposals.

## Project Inspector

`praxile.inspector` detects local stack markers during `praxile init` and `praxile doctor`.

It currently recognizes:

- Python projects and pytest/unittest defaults;
- Node package scripts, package manager lockfiles, React/Vite/Next/TypeScript markers;
- Go modules;
- Rust crates.

The inspector does not execute project code. It only suggests verification commands, records metadata in `.praxile/config.json`, and reports missing local tools.

## Tools

`praxile.tools.ToolRegistry` owns safe action dispatch for:

- `list_files`;
- `project_map`, `list_dir`, and `find_files`;
- `search`;
- `read_file`;
- `read_files`;
- `batch` for concurrent read-only actions;
- `browser_open` and `browser_screenshot` when the optional browser adapter is enabled;
- `edit_file`;
- `run_command`;
- `finish`.

The registry delegates to FileSystemEnv, GitEnv, ShellEnv, and TestEnv behind the safety policy.

Experience retrieval combines SQLite assets, FTS, and optional vector rows. The local vector table is maintained with the same index lifecycle as other assets.

## Skills

`praxile.skills.SkillSystem` owns project-local skill discovery and loading.

Skills live at:

```text
.praxile/skills/<skill-name>/SKILL.md
.praxile/skills/<skill-name>/metadata.json
.praxile/skills/<skill-name>/versions/<version>.md
```

They are loaded by Praxile, not by external frameworks. Runtime loads only active skills; deprecated/replaced skills remain auditable but are removed from retrieval. Durable skill changes are proposal-driven.

## Memory

`praxile.memory.MemorySystem` owns project-local memory:

- `user.md`;
- `project.md`;
- `decisions.md`;
- `failures.md`.

Memory remains repository scoped. External/global export requires a future explicit proposal.

## Gateway

`praxile.gateway` provides an optional stdlib HTTP gateway:

- `GET /health`;
- `GET /history`;
- `GET /memory`;
- `GET /channels`;
- `POST /run`;
- `GET /review?id=...`;
- `POST /accept`;
- `POST /channels/bind`;
- `POST /channels/unbind`;

The gateway is local-first and optional. It is a programmatic frontend to Praxile, not a dependency.

The same gateway serves a local web console at `/`. The console submits tasks and proposal decisions through the API; it does not write durable experience assets directly.

## Terminal

`praxile.terminal.TerminalSession` provides the interactive `praxile terminal` surface.

It supports:

- task submission;
- latest trajectory/proposal review;
- pending proposal queue listing;
- proposal accept/reject;
- history;
- project memory retrieval;
- channel listing.

It is intentionally not a raw unrestricted shell. Commands are routed through Praxile runtime and store APIs so safety checks, trajectory logging, reward reports, proposals, approval, and rollback remain intact.

## Channels

`praxile.channels.ChannelSystem` owns Telegram/Discord binding metadata in `.praxile/config.json`.

Bindings include:

- platform and channel identifiers;
- token environment variable name;
- home/project/review/alert kind;
- notify/task/bidirectional mode;
- mention and free-response policy;
- optional channel-specific skill and prompt.

Praxile stores route metadata only. Raw bot tokens remain in environment variables such as `TELEGRAM_BOT_TOKEN` and `DISCORD_BOT_TOKEN`.
