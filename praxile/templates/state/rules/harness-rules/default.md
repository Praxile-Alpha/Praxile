# Default Harness Rules

- Durable memory, skill, eval, routing, and rule updates must be proposals until accepted.
- Objective signals such as tests, lint, build, diff scope, safety blocks, and rollback backups have priority over LLM-only judgement.
- UX-sensitive tasks require a human acceptance checklist even when automated tests pass.
- High-risk architecture work requires the architecture gate before normal implementation.
- Privacy-sensitive tasks should prefer the configured local/private model route when available.
- Repeated safety blocks should produce a failure-pattern or harness-rule proposal instead of broadening permissions casually.
