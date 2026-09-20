# P2-D: Executable Harness Lab

P2-D turns a Harness Candidate from a policy document into a frozen executable experiment. It reuses the V2 Agent Adapter, Event Store, detached-worktree benchmark runner, independent evaluator, and six promotion gates rather than creating a second execution stack.

## Frozen Experiment

An `ExecutableHarnessManifest` binds all of the following before execution:

- Creation or Evolution mode;
- worktree or capability-verified container isolation;
- candidate and baseline Harness policies;
- exact adapter/executor profile, model identity, evaluator identity, and task family;
- disjoint development and held-out task IDs;
- at least two repetitions;
- declared mechanisms and their expected runtime coverage.

The manifest is immutable once written under `.praxile/eval/v2/harness-lab/<LAB_ID>/manifest.json`. Each task/arm/repetition is delegated to `BenchmarkEvalRunner`, which creates a detached Git worktree. `container` mode is accepted only when the Adapter declares `capabilities.metadata.isolation_mode=container`; a configuration string cannot claim container isolation.

Creation compares a new Harness against a default baseline and has no base Harness reference. Evolution requires a versioned `base_harness_ref` and compares that executable base policy with the candidate policy.

## Runtime Coverage

Praxile reports six runtime coverage classes:

| Class | Meaning |
|---|---|
| E | Execution and environment lifecycle |
| T | Model, tool, and artifact interaction |
| C | Context activation, representation, usage, and injection |
| S | Skill and subagent execution |
| L | The versioned Harness Candidate was applied to an executable arm |
| V | An independent evaluator outcome was produced |

Every manifest declares its mechanisms. A required mechanism with zero matching activation across all candidate repetitions is a `dead_mechanism` and blocks promotion eligibility. Mechanisms may name exact expected event types when class-level coverage is too broad.

## Repetition and Promotion

Development and held-out results remain separate. Resolution rates use 95% Wilson intervals; cost and token deltas include repeated-run mean intervals. A positive point estimate is insufficient: Harness Lab promotion evidence requires a non-negative held-out lower confidence bound, no required dead mechanisms, valid invariants, and no regression beyond policy.

The Lab report feeds the existing six gates: Evidence, Quality, Regression, Cost, Human, and Rollback. It does not promote itself.

Promotion state is now scoped by:

```text
component_key + executor_profile + task_family
```

V1 registries migrate legacy active pointers to `component::default::default`. This prevents a gain measured with one executor/model profile from silently becoming active for another profile or task family.

## Commands

Start from [the example manifest](candidates/P2_D_HARNESS_LAB.example.json), then run:

```bash
praxile harness lab-digest dev-task-set.json
praxile harness lab-digest heldout-task-set.json

praxile harness lab-run harness-lab.json \
  --development-tasks dev-task-set.json \
  --heldout-tasks heldout-task-set.json \
  --source owner/repo=/path/to/local/repo

praxile harness candidate-evaluate CANDIDATE_ID \
  --ab-report .praxile/eval/v2/harness-lab/LAB_ID/report.json \
  --reviewer maintainer --approve-human

praxile harness lab-matrix \
  report-executor-a.json report-executor-b.json \
  --output executor-matrix.json
```

Replace the example evaluator's `distribution_version` with the installed `swebench` package version, and replace both all-zero task-set digests with the `EvalTaskSet.digest` values produced for the frozen files. Evaluator identity, task content, base commits, timeout, and extra arguments are compared exactly; this is intentional reproducibility enforcement.

The CLI `lab-run` currently constructs the mini-SWE-agent adapter and official SWE-bench evaluator. The Python API accepts any Agent Adapter V2 and evaluator that match the frozen manifest.

## Security Boundary

Detached worktrees prevent source-checkout mutation but are not a host security sandbox. Container isolation is real only when provided and declared by the executor Adapter. Held-out answers and evaluator payloads remain control-plane-owned and are never projected into `AdapterTask`; a same-UID malicious process still requires OS/container or remote isolation, as documented in P2-A.
