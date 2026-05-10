# Product Vision

Praxile exists because coding agents should not forget the useful parts of real project work.

A normal agent run completes a task and leaves a transcript. Praxile turns the run into governed project experience:

1. interact with the local code environment;
2. record a trajectory;
3. score the result with objective and human-review signals;
4. extract reusable experience proposals;
5. apply only the proposals the user approves;
6. retrieve accepted experience in later similar tasks.

The long-term direction is a local project intelligence layer: each repository accumulates its own rules, memories, skills, evals, failure patterns, frozen boundaries, and model-routing lessons. Updates are auditable diffs, not silent self-modification.

Spec files can help Praxile understand intent, acceptance criteria, and non-goals, but Praxile is not trying to become another spec generator. Its durable product surface is governed experience: evidence-backed memories, skills, rules, evals, failure patterns, and boundaries that make the next run safer and more project-aware.

P2 deepens that loop by treating experience quality as a product surface. Proposals carry source evidence, confidence, applicability scope, and anti-scope. Skills have explicit lifecycle metadata and version snapshots. Failure patterns are structured enough to become searchable guardrails. Model routing performance can be summarized from trajectories, and consolidation creates proposal-only cleanup suggestions before accumulated experience becomes noisy.

The first version deliberately avoids automatic model training, multi-agent orchestration, marketplace behavior, and global memory writes. Trust, reviewability, and rollback come first.
