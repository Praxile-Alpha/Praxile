# Install And Interop

Praxile should be published and used as an independent local self-evolving agent harness for code projects.

It is not a fork-only feature, plugin, or thin wrapper around Hermes or OpenClaw. Praxile owns the Environment -> Reward -> Experience loop. Hermes, OpenClaw, local models, and cloud endpoints are optional adapters or endpoint sources. The durable project state remains in `.praxile/` inside the user's code repository.

## Recommended User Install

Prerequisite: Python 3.11 or newer.

For end users, the intended release shape is:

```bash
pipx install praxile
```

or:

```bash
uv tool install praxile
```

Then, inside any code repository:

```bash
praxile init
praxile doctor
praxile run "Fix the failing parser test" --test-command "python -m pytest"
praxile review
praxile accept <PROPOSAL_ID>
```

Users who want an OpenClaw-like local work surface can use:

```bash
praxile terminal
```

or:

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
```

and open `http://127.0.0.1:8765/`.

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

The user's target project does not need to be a Hermes or OpenClaw project. Praxile can run as a standalone CLI with an OpenAI-compatible endpoint, a local model endpoint, or configured cloud provider routes.

## Development Install

For contributors:

```bash
git clone <praxile-repo-url>
cd praxile
python -m pip install -e .
praxile --help
```

This repository is the standalone Praxile shape. Contributors should expect:

```text
praxile/
tests/
pyproject.toml
README.md
LICENSE
docs/
```

The Python package remains `praxile`, and the CLI entrypoint remains `praxile`.

## Model Provider Connection

The first release should support three common paths:

### Standalone OpenAI-Compatible Endpoint

Users can point Praxile at any OpenAI-compatible local or cloud endpoint during setup:

```bash
praxile init
praxile setup --provider ollama --base-url http://localhost:11434/v1 --model qwen2.5-coder:7b --channel none
praxile doctor --online
```

Fresh `.praxile/config.json` starts without provider choices. `praxile setup` writes model providers and role routing only after the user chooses them. Praxile owns routing policy for task types such as planning, coding, private work, cheap summarization, and experience extraction.

### Existing Hermes User

If Hermes is already installed in the same Python environment, Praxile can detect Hermes capability modules with:

```bash
praxile interop
```

Current MVP behavior:

- detects Hermes provider/runtime/tool/skill/memory/gateway/trajectory/setup modules when importable;
- reports the detected capabilities in `praxile interop` and `praxile doctor`;
- keeps `.praxile/skills` separate from Hermes global/native skills;
- keeps `.praxile/memory` separate from Hermes persistent/global memory;
- writes Praxile trajectories as canonical JSON plus an external-compatible JSONL sidecar.

Current MVP non-goals:

- does not monkey-patch Hermes runtime;
- does not install project-local Praxile skills into Hermes;
- does not write project facts into Hermes global memory;
- does not depend on Hermes gateway sessions;
- does not require users to run `hermes`.

The intended command flow for an existing Hermes user is:

```bash
cd my-code-repo
praxile init
praxile interop
praxile run "Implement the requested fix" --test-command "python -m pytest"
praxile review
praxile accept <PROPOSAL_ID>
```

Hermes remains available as `hermes`. Praxile remains available as `praxile`.

### Existing OpenClaw User

For OpenClaw users, the practical first-release connection should be provider-level rather than memory/skill-level:

1. keep using the user's existing OpenAI-compatible or local model endpoint;
2. configure Praxile to point at that endpoint;
3. run Praxile inside the target code repository;
4. keep `.praxile/` state separate from OpenClaw state unless an explicit adapter is added.

Intended first flow:

```bash
cd my-code-repo
praxile init
# edit .praxile/config.json if the OpenClaw model endpoint differs
praxile doctor
praxile run "Fix a small bug" --test-command "python -m pytest"
```

OpenClaw-specific memory, skills, trajectory formats, or runtime hooks should require a named adapter, for example a future:

```bash
praxile attach openclaw
```

That command is not implemented in the MVP. Until then, OpenClaw interop should be described as endpoint/config compatibility, not native runtime integration.

## Interop Contract

Praxile should use an explicit adapter contract instead of depending on external agent framework behavior.

```text
Praxile Agent Core
  owns runtime, environment adapters, .praxile state, reward, experience, proposals, approval, rollback, gates

Provider Adapter
  connects to OpenAI-compatible, local, or cloud model endpoints

Optional Framework Adapter
  detects selected Hermes/OpenClaw capabilities when explicitly supported

Project Repository
  receives .praxile/ local state and normal code edits
```

Adapter rules:

- Detection is safe and read-only by default.
- Project-local memory never becomes external global memory automatically.
- Project-local skills never become external native skills automatically.
- Trajectory sidecars are exports, not the canonical audit log.
- Any future sync/export/import operation must create an auditable proposal.

## Public Release Checklist

Before publishing a release:

- keep the repository root focused on `praxile/`, `tests/`, `docs/`, `pyproject.toml`, `README.md`, and `LICENSE`;
- publish as a package named `praxile`;
- keep the CLI command `praxile`;
- document standalone install with `pipx` and `uv tool`;
- include `.praxile/` in the generated project-local state docs;
- make Hermes support an optional detected adapter, not a required dependency;
- describe OpenClaw support as endpoint-compatible until a native adapter exists;
- add `praxile doctor` checks for provider reachability, model routes, state layout, safe command policy, and adapter detection.
