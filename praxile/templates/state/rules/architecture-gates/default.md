# Default Architecture Gate

## Gate behavior

Praxile must pause normal implementation before editing files when a task touches shared contracts, core data flows, auth/session logic, routing, storage, migrations, public APIs, or accepted frozen boundaries.

Until the gate proposal is reviewed and accepted, the task must not continue as an ordinary feature patch. The agent may inspect files, collect evidence, and produce a reviewable proposal, but it must not land implementation edits.

## Required proposal fields

- Impact scope
- Alternatives considered
- Migration path
- Rollback plan
- Validation strategy
- Human approval status

