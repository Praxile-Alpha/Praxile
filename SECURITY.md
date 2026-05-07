# Security

Praxile runs in local code repositories and can read, edit, test, and propose durable project-local updates. Treat it as a developer tool with repository access.

## Safety Model

- Sensitive files such as `.env`, private keys, cloud credentials, and SSH/AWS directories are protected by default.
- Dangerous shell patterns such as recursive deletion, privilege escalation, disk erase commands, and broad git destructive commands are blocked by default.
- Durable self-evolution writes are proposals until explicitly accepted by the user.
- Accepted proposal writes are restricted to clean relative paths under project-local `.praxile/` asset roots such as `memory/`, `skills/`, `evals/`, `rules/`, and `experience/failures/`.
- Accepted proposals record before/after snapshots and can be rolled back.
- Non-localhost gateway binds require an explicit token.

## Reporting

Please report vulnerabilities privately to the project maintainers before public disclosure.

Include:

- Praxile version or commit;
- operating system;
- exact command or workflow;
- expected behavior;
- observed behavior;
- whether sensitive files, command safety, proposal approval, gateway auth, or rollback are involved.

## Non-Goals For Alpha

The Alpha release does not attempt model weight training, marketplace distribution, production channel listeners, or automatic synchronization with external agent-framework memory stores.
