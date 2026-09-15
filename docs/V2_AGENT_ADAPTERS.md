# Praxile V2 Agent Adapters

Status: versioned protocol, Fixture Adapter, and mini-SWE-agent subprocess adapter implemented
Last updated: 2026-09-11

## Contract

`praxile.adapters.AgentAdapterV2` is the execution-plane boundary. Its frozen protocol version is:

```text
praxile.agent_adapter.v2
```

An adapter implements:

```python
run(task, policy) -> RunHandle
stream_events(run_handle) -> Iterator[AgentEvent]
get_artifacts(run_handle) -> list[ArtifactRecord]
cancel(run_handle) -> None
capabilities() -> AdapterCapabilities
```

`AdapterTask` and `AdapterPolicy` are validated, JSON-serializable inputs. The policy carries bounded context, budgets, and execution settings without exposing Praxile's internal stores to the external agent. `AdapterRunner` validates run, event, child-run, and artifact identities before committing evidence to `EventStore`.

The legacy `AgentAdapter` remains a V1 format-conversion interface. It was not changed in place because existing JSONL and interoperability consumers still depend on it.

## Fixture Adapter

`FixtureAgentAdapter` is deterministic and has no model, network, or subprocess dependency. It exists for adapter conformance, Event Store integration, cancellation, context-injection, artifact-linkage, and future eval-runner tests.

```python
from praxile.adapters import (
    AdapterPolicy,
    AdapterRunner,
    AdapterTask,
    FixtureAgentAdapter,
    FixtureEvent,
)
from praxile.config import Config
from praxile.trace import EventStore

store = EventStore(Config.load(project_root).paths)
adapter = FixtureAgentAdapter(
    events=[FixtureEvent("VERIFICATION", payload={"passed": True})]
)
result = AdapterRunner(store).execute(
    adapter,
    AdapterTask("task_1", "Fix the parser", str(project_root)),
    AdapterPolicy(policy_id="baseline"),
)
```

## mini-SWE-agent Adapter

Install the optional runtime:

```bash
python -m pip install -e ".[mini-swe]"
```

The adapter uses mini-SWE-agent's supported `mini` CLI and its saved trajectory rather than importing or modifying mini-SWE-agent's internal loop. It passes `--task`, `--output`, `--yolo`, and `--exit-immediately`, with optional model, config, and cost-limit arguments. If custom config specs are supplied, include mini-SWE-agent's default config explicitly as required by its CLI. File-based mini-SWE-agent configs must use its supported `.yaml` suffix; key-value config specs remain valid.

When an `AdapterPolicy` was compiled from a V2 Context Policy, the runner emits
one `CONTEXT_SOURCE_USAGE` event per declared source after `RUN_START`. The event
records selection count, source budget utilization, compression profile,
before/after token estimates, and the decision reason. Reserved
`_praxile_measurement` input is removed before context reaches the adapter.

```python
from praxile.adapters import AdapterPolicy, AdapterRunner, AdapterTask, MiniSweAgentAdapter

adapter = MiniSweAgentAdapter(model="openai/gpt-5")
policy = AdapterPolicy(
    policy_id="baseline",
    budgets={"max_cost": 2.0, "wall_timeout_seconds": 1800},
    settings={
        "allow_unattended_execution": True,
        "workspace_isolated": True,
        "verification_commands": ["python -m pytest -q"],
    },
)
result = AdapterRunner(store).execute(
    adapter,
    AdapterTask("task_1", "Fix the parser", str(isolated_checkout)),
    policy,
)
```

### Safety boundary

The subprocess adapter refuses to start unless both `allow_unattended_execution` and `workspace_isolated` are true. This is intentional: the non-interactive integration uses mini-SWE-agent's yolo mode, which executes proposed actions without per-command confirmation. Praxile does not infer isolation from a directory name. The caller is responsible for preparing a disposable checkout, container, or equivalent sandbox.

The process is launched with an argument vector and `shell=False`. Cancellation and timeouts terminate the process group. The adapter supports explicit `close()` and context-manager cleanup. For unattended runs it also sets mini-SWE-agent's `MSWEA_CONFIGURED` flag and defaults `MSWEA_GLOBAL_CONFIG_DIR` to a run-local directory, preventing a first-run setup prompt or an implicit write to the user's global config. Explicit adapter environment settings can override these values.

### Trace fidelity

mini-SWE-agent V2 keeps model-specific native message shapes and places normalized execution metadata in `extra`. Praxile therefore:

- preserves the complete native `trajectory.traj.json` as a content-hashed artifact;
- maps assistant/model messages, `extra.actions` or native tool calls, observations, cost, and token usage when present;
- preserves rejected model responses from mini-SWE-agent `FormatError` records as `MODEL_CALL` events with `parse_status=rejected`;
- maps exact policy-declared verification commands to `VERIFICATION` events without heuristic command classification;
- captures the tracked Git working-tree diff as a content-hashed `workspace_patch` artifact when one exists;
- records the native trajectory format and exit status;
- declares `event_streaming=false` and `stream_mode=post_run_trajectory` because the current CLI bridge converts the saved trajectory after the process exits.

This adapter is an execution and evidence bridge. The separate P0-B benchmark control path is documented in [V2 Eval Runner](V2_EVAL_RUNNER.md); baseline/candidate comparison remains a P0-C responsibility.

For a local model that does not support native tool calling, select mini-SWE-agent's text protocol explicitly, for example `MiniSweAgentAdapter(model="ollama/deepseek-coder:6.7b", model_class="litellm_textbased", config_specs=["mini_textbased.yaml"])`. If the local model has no price metadata, its run environment may explicitly set `MSWEA_COST_TRACKING=ignore_errors`; do not apply that override to priced cloud-model runs because it could hide a real cost-accounting failure.

### Local workspace contract and preflight

`MiniSweAgentAdapter` invokes the local `mini` CLI. It now pins mini-SWE-agent's
environment class and `environment.cwd` to the isolated repository root and
injects the same root into the task instruction. A benchmark config that binds
the agent to `/testbed` is rejected in this mode; use a future dedicated
container adapter instead of combining a container prompt with local execution.

Before model execution, the adapter writes `preflight.json` and emits a
`PREFLIGHT` event. Benchmark commands require the prepared workspace to be the
exact Git root so patch capture cannot escape into a parent monorepo. A failed
preflight does not launch the model, but still produces a bounded trace and is
diagnosed as `ENVIRONMENT / workspace_preflight_failed`.

Each completed run also emits `PROGRESS` with environment-probe count, repeated
command count, an explicitly heuristic first repository-write action, and the
objectively measured final patch presence. These fields explain whether a step
limit was spent on setup, exploration, implementation, or patch materialization.

### Bounded events and full native evidence

The native `trajectory.traj.json` artifact remains byte-for-byte audit evidence.
Normalized `MODEL_CALL` and `TOOL_RESULT` events are intentionally bounded:
large strings are shortened, and `extra.raw_output` becomes a small
`raw_output_meta` record containing its original size, preview, and a pointer to
the native trajectory artifact. Event Store replay therefore stays usable even
when a base agent records multi-megabyte command output, without deleting the
full evidence needed for forensic review.

### Runtime stopping control

Benchmark policies enable a hard stopping monitor around the mini-SWE-agent
process. The adapter reads each atomically readable trajectory update and checks
the isolated Git workspace. It terminates the process when the no-patch
exploration budget is exhausted, the post-patch budget is exhausted, a patch has
been successfully verified and consumed its grace steps, or an already verified
command repeats beyond policy. The decision is persisted as `STOP_DECISION` and
the final adapter status is `policy_stopped`. This status is not task success:
the official evaluator still decides whether the captured patch resolves the
benchmark task.

### Context activation boundary

Praxile resolves candidate context before invoking an adapter. A versioned
activation plan compares the task repository and semantic terms with the
candidate's `applies_to.repositories`, `task_signals`, and
`does_not_apply_when`. The shared `AdapterRunner` records the decision as
`CONTEXT_ACTIVATION`; an abstained task receives an empty effective context and
therefore no `CONTEXT_INJECT`. Adapters never decide for themselves whether a
candidate applies.

## Verification

The generic adapter protocol tests remain in the default suite:

```bash
python -B -m pytest -q tests/unit/test_adapter_v2.py
```

All mini-SWE-agent adapter, fixture subprocess, and published-evidence tests are
kept behind a separate opt-in target:

```bash
make test-mini-swe
```

This target does not contact a model provider or run the real SWE-bench
evaluator. Real A/B experiments are manual operations and must use an isolated
workspace.
