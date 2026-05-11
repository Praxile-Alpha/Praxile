# Security Model

Praxile runs in local code repositories and can read, edit, test, and propose durable project-local updates. Treat it as a developer tool with repository access. It is designed with a highly conservative, constraint-first safety model to prevent catastrophic code or system damage.

## 1. Safety Policy

Praxile enforces the following guardrails by default:

- **Path Restrictions**: Path access must remain inside the selected project root. Writes anywhere under `.praxile/` are blocked through normal agent file actions.
- **Sensitive Files Protected**: Files such as `.env`, `.env.*`, private keys, `.pem`, `.key`, `.p12`, `.pfx`, `.aws/`, `.ssh/`, secrets, and credential-like filenames are blocked from being read or modified by the agent.
- **Dangerous Commands Blocked**: Dangerous shell patterns are blocked by default. This includes `rm -rf`, `sudo`, `su`, `dd if=`, `mkfs`, recursive `chmod`/`chown`, `git reset`, `git clean`, shutdown/reboot, and pipe-to-shell installers.
- **Strict Shell Mode**: Compound shell commands, command substitution, most pipes, and unapproved command prefixes are blocked in safe mode. If `shell.allow_shell_features=true` is enabled, execution is still limited to reviewed prefixes and project-local redirection targets.
- **Allowed Commands**: Allowed commands default to safe test/lint/build/status operations such as `python -m pytest`, `npm test`, `npm run lint`, `npm run build`, `cargo test`, `go test`, `git status`, and `git diff`.

## 2. Governed Experience & State Security

Praxile does not allow the agent to silently modify its own memory or rules:

- **Proposals over Writes**: Durable self-evolution writes (memories, skills, rules) are strictly generated as **Proposals** until explicitly accepted by the user.
- **Asset Boundaries**: Accepted proposal writes are restricted to clean relative paths under project-local `.praxile/` asset roots such as `memory/`, `skills/`, `evals/`, `rules/`, and `experience/failures/`.
- **Experience Constitution**: The `.praxile/constitution.md` acts as a hard governance layer. Proposals violating the constitution (e.g., lacking scope/anti-scope, no evidence) will be flagged by the Proposal Gate.

## 3. Rollback & Recovery

Every destructive action in Praxile is reversible:
- **File Edit Backups**: File edits are backed up before write, capped by backup retention settings (`safety.backup_max_files`), and can be restored with `praxile rollback <TASK_ID>`.
- **Proposal Rollbacks**: Accepted proposals store before/after snapshots and can be reverted using `praxile rollback <PROPOSAL_ID>`.
- **State Integrity**: State files and JSONL append paths use cross-platform file locks to prevent corruption during concurrent agent edits.

## 4. Architecture Gates & Silent Failures

Praxile prevents AI from causing "silent architectural damage":
- **Architecture Gates**: If a task touches shared contracts, auth/session, routing, storage, migrations, or frozen boundaries, Praxile pauses implementation and records an `architecture_gate` action. The task cannot proceed until the human reviews the gate.
- **Silent Failure Detection**: The runtime detects risky behaviors like `no_tests_but_completed` (claiming success without running tests) or `broad_diff_without_spec` (massive changes without an explicit Spec). These trigger warnings and lower proposal confidence.

## 5. Interop Guardrails

If Praxile detects external agent framework lock files (e.g., `.hermes/agent.lock`, `.openclaw/agent.lock`, `.agent.lock`) or environment flags (`HERMES_AGENT_ACTIVE`), it will refuse normal project writes to prevent race conditions and conflicting agent behaviors.

## 6. Gateway & Channel Security

- Non-localhost gateway binds (`0.0.0.0`) are refused unless an explicit `--token` is provided.
- Web consoles and API clients must authenticate using `Authorization` or `X-Praxile-Token` headers when exposed to a network.

## Reporting Vulnerabilities

Please report vulnerabilities privately to the project maintainers before public disclosure. Include the Praxile version, OS, exact command, expected/observed behavior, and the security layer involved.