# Default Architecture Gate

Trigger this gate before changing shared state shape, cross-module contracts, authentication, permission, routing, persistence, migrations, core data flows, or an accepted frozen boundary.

Required output:

- goal;
- why a local patch is insufficient;
- affected modules;
- alternatives;
- minimal migration path;
- rollback plan;
- verification plan;
- whether a frozen boundary should be added or updated.

Gate behavior:

- pause normal implementation before file edits;
- record an `architecture_gate` action in the trajectory;
- generate an architecture gate proposal;
- do not continue as an ordinary feature patch until the gate has been reviewed and accepted.
