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

## The Next Product Boundary

The agent ecosystem is rapidly standardizing tool access, long-running execution, checkpointing, human approval, skills, and memory. Praxile should not compete by reproducing every office or coding-agent surface. Its durable product boundary is **trusted harness evolution**:

```text
Execution Evidence
  -> Failure Diagnosis
  -> Minimal Harness Proposal
  -> Shadow / Regression Validation
  -> Human Approval
  -> Versioned Activation
  -> Outcome Attribution
```

Four principles define this boundary:

1. **Proposal is not proof.** An LLM may diagnose and propose, but deterministic checks, sealed evals, calibrated judges, and human review decide whether an update receives credit.
2. **Stored is not learned.** Praxile must measure retrieval, prompt injection, agent reference, behavioral compliance, and outcome contribution separately.
3. **Improvement must generalize.** A proposal that fixes its source episode but regresses held-out cases is not a durable improvement.
4. **The outer governance anchor stays frozen.** Safety policy, approval requirements, evidence requirements, rollback guarantees, and sealed evaluation ownership cannot be rewritten by ordinary experience proposals.

This direction is described in [Agent Harness Landscape 2026](AGENT_HARNESS_LANDSCAPE_2026.md) and translated into engineering work in [Trusted Harness Evolution Roadmap](TRUSTED_HARNESS_EVOLUTION_ROADMAP.md).
