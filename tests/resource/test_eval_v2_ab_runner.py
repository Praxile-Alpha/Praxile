from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter, FixtureArtifact, FixtureEvent
from praxile.config import Config
from praxile.eval.v2 import (
    CapabilityProtocol,
    ContextPolicyAblation,
    ContextCandidate,
    ControlledABExperiment,
    EvalTask,
    EvalTaskSet,
    EvalSchemaError,
    EvaluatorResult,
    RepositorySpec,
    PublicExperimentExporter,
    ProxyEvalProposal,
    ProxyEvalRegistry,
    SWEbenchEvaluationSpec,
    SWEbenchPrediction,
)
from praxile.control_plane import ContextPolicy, ContextSourceRule, StageBudget
from praxile.trace import AgentEvent, EventStore
from praxile.utils import read_json, utc_now


pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


class PassingEvaluator:
    name = "passing"
    version = "1"

    def identity(self) -> Mapping[str, Any]:
        return {"name": self.name, "version": self.version}

    def evaluate(self, task: EvalTask, prediction: SWEbenchPrediction, output_root: Path) -> EvaluatorResult:
        output_root.mkdir(parents=True, exist_ok=True)
        evidence = output_root / "result.json"
        evidence.write_text('{"resolved": true}\n', encoding="utf-8")
        now = utc_now()
        return EvaluatorResult("completed", True, now, now, (str(evidence),), {})


def _repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Praxile Tests"], cwd=path, check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "candidate.patch").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_controlled_ab_runs_one_clean_context_variable_and_persists_diagnoses(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _repo(source)
    task = EvalTask(
        task_id="owner__repo-1",
        instruction="Fix the issue",
        repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )
    task_set = EvalTaskSet("held-out", "fixture", "test", (task,))
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    adapter = FixtureAgentAdapter(
        events=[FixtureEvent("TOOL_CALL", payload={"tool": "edit"})],
        artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)],
    )
    candidate = ContextCandidate(
        candidate_id="focused-context",
        version="1",
        title="Focus repository exploration",
        candidate_type="experience_activation",
        context_item={"asset_id": "pattern-1", "content": "Start with one focused regression test."},
        confidence=0.7,
        evidence_refs=("event:event-training",),
        expected_effect={"tool_calls": "decrease"},
        source_diagnosis_ids=("diagnosis-training",),
        source_task_ids=("training-task",),
    )
    protocol = CapabilityProtocol.from_dict({
        "schema_version": "praxile.capability_protocol.v1",
        "goal": {"schema_version": "praxile.capability_goal.v1", "goal_id": "fixture-goal", "version": "1", "task_family": "bugfix", "objective": "Resolve the held-out task", "success_criteria": ["heldout_resolution"]},
        "operationalization": {"schema_version": "praxile.operationalization_hypothesis.v1", "hypothesis_id": "focused", "version": "1", "goal_id": "fixture-goal", "rationale": "Focused reads may reduce tool calls", "proxy_metrics": ["tool_calls"], "known_failure_modes": ["no resolution gain"]},
        "evaluation": {"schema_version": "praxile.evaluation_contract.v1", "contract_id": "fixture-eval", "version": "1", "goal_id": "fixture-goal", "dataset_name": "fixture", "split": "test", "development_task_ids": [], "heldout_task_ids": ["owner__repo-1"], "evaluator_owner": "control_plane", "evaluator_name": "passing", "primary_metric": "resolved"},
        "information_boundary": {"schema_version": "praxile.information_boundary.v1", "boundary_id": "fixture-boundary", "version": "1", "isolation_level": "logical_only", "forbidden_metadata_keys": []},
        "terminal_selection_rule": {"schema_version": "praxile.terminal_selection_rule.v1", "rule_id": "heldout-first", "version": "1", "minimum_heldout_gains": 1, "maximum_heldout_regressions": 0, "require_human_approval": True},
    })
    config = Config.load(tmp_path / "control")
    state = tmp_path / "state"
    store = EventStore(config.paths)

    experiment = ControlledABExperiment(state, store)
    report = experiment.run(
        task_set,
        adapter=adapter,
        evaluator=PassingEvaluator(),
        baseline_policy=AdapterPolicy(
            policy_id="p0-baseline",
            budgets={"wall_timeout_seconds": 30},
            settings={"workspace_isolated": True, "step_limit": 50},
        ),
        candidate=candidate,
        model={"model_name_or_path": "fixture", "cost_tracking": "default"},
        experiment_id="p0-c-fixture",
        source_overrides={"owner/repo": source},
        capability_protocol=protocol,
    )

    assert report["invariant_check"]["valid"] is True
    assert report["comparison"]["decision"] == "inconclusive"
    assert report["capability"]["terminal_selection"]["decision"] == "inconclusive"
    assert report["capability"]["information_boundary"]["os_sandbox"] is False
    assert report["comparison"]["trace_overhead"]["context_inject_event_delta"] == 1
    assert report["activation_gate"]["activated_count"] == 1
    assert report["activation_gate"]["abstained_count"] == 0
    assert report["comparison"]["totals"]["candidate_context_activated"] == 1
    candidate_run = report["candidate"]["eval_run_id"]
    candidate_report = read_json(state / "eval" / "v2" / "runs" / candidate_run / "report.json", {})
    candidate_trace = candidate_report["tasks"][0]["trace_id"]
    candidate_events = store.list_events(trace_id=candidate_trace)
    assert any(event.type == "CONTEXT_ACTIVATION" for event in candidate_events)
    assert any(event.type == "CONTEXT_INJECT" for event in candidate_events)
    assert report["diagnoses"]["baseline"][0]["outcome"] == "no_failure"
    assert report["diagnoses"]["candidate"][0]["attribution"]["abstained"] is True
    root = state / "eval" / "v2" / "experiments" / "p0-c-fixture"
    assert (root / "manifest.json").is_file()
    assert (root / "report.json").is_file()
    assert (root / "diagnoses" / "baseline" / "owner__repo-1.json").is_file()

    reanalyzed = experiment.analyze("p0-c-fixture")
    assert reanalyzed["capability"]["protocol_digest"] == protocol.digest

    assert reanalyzed["diagnoses"]["baseline"][0]["diagnosis_id"] == (
        report["diagnoses"]["baseline"][0]["diagnosis_id"]
    )
    assert reanalyzed["diagnoses"]["candidate"][0]["diagnosis_id"] == (
        report["diagnoses"]["candidate"][0]["diagnosis_id"]
    )

    public = tmp_path / "public"
    paths = PublicExperimentExporter(state, store).export("p0-c-fixture", public)
    manifest_text = Path(paths["manifest"]).read_text(encoding="utf-8")
    metrics_text = Path(paths["metrics"]).read_text(encoding="utf-8")
    trace_text = Path(paths["trace_sample"]).read_text(encoding="utf-8")

    assert str(tmp_path) not in manifest_text
    assert "<redacted:content>" in trace_text
    assert "Fix the issue" not in trace_text
    assert "Start with one focused regression test." not in trace_text
    assert '"tokens"' in metrics_text
    assert '"terminal_selection"' in metrics_text
    assert protocol.digest in manifest_text
    assert '"tokens": "<redacted:credential>"' not in metrics_text
    assert not list(public.glob("*.lock"))
    for line in trace_text.splitlines():
        event = AgentEvent.from_json(line)
        assert event.extensions["public_export"]["redacted"] is True


def test_capability_ab_uses_approved_dev_proxy_and_seals_heldout_reuse(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _repo(source)
    tasks = tuple(
        EvalTask(
            task_id=task_id,
            instruction="Fix the issue",
            repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
            evaluation=SWEbenchEvaluationSpec("fixture", "test"),
        )
        for task_id in ("dev-task", "heldout-task")
    )
    task_set = EvalTaskSet("fixture", "fixture", "test", tasks)
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    adapter = FixtureAgentAdapter(artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)])
    candidate = ContextCandidate(
        candidate_id="focused-context", version="1", title="Focused context",
        candidate_type="experience_activation", context_item={"content": "Read the focused test first."},
        confidence=0.7, evidence_refs=("event:source",),
        expected_effect={"tool_calls": "decrease"}, source_task_ids=("source-task",),
    )
    protocol = CapabilityProtocol.from_dict({
        "schema_version": "praxile.capability_protocol.v1",
        "goal": {"schema_version": "praxile.capability_goal.v1", "goal_id": "fixture-goal", "version": "1", "task_family": "bugfix", "objective": "Resolve held-out tasks", "success_criteria": ["heldout_resolution"]},
        "operationalization": {"schema_version": "praxile.operationalization_hypothesis.v1", "hypothesis_id": "focused", "version": "1", "goal_id": "fixture-goal", "rationale": "Focused reads might save calls", "proxy_metrics": ["tool_calls"], "known_failure_modes": ["no resolution gain"]},
        "evaluation": {"schema_version": "praxile.evaluation_contract.v1", "contract_id": "fixture-eval", "version": "1", "goal_id": "fixture-goal", "dataset_name": "fixture", "split": "test", "development_task_ids": ["dev-task"], "heldout_task_ids": ["heldout-task"], "evaluator_owner": "control_plane", "evaluator_name": "passing", "primary_metric": "resolved"},
        "information_boundary": {"schema_version": "praxile.information_boundary.v1", "boundary_id": "logical", "version": "1", "isolation_level": "logical_only", "forbidden_metadata_keys": []},
        "terminal_selection_rule": {"schema_version": "praxile.terminal_selection_rule.v1", "rule_id": "heldout-first", "version": "1", "minimum_heldout_gains": 1, "maximum_heldout_regressions": 0, "require_human_approval": True},
    })
    config = Config.load(tmp_path / "control")
    state = tmp_path / "state"
    proxy_registry = ProxyEvalRegistry(state)
    proxy = ProxyEvalProposal.from_dict({
        "schema_version": "praxile.proxy_eval.v1", "proxy_id": "dev-efficiency", "version": "1",
        "hypothesis_id": "focused", "task_ids": ["dev-task"],
        "rationale": "Development calls must not increase",
        "checks": [{"metric": "tool_call_delta", "operator": "<=", "threshold": 0}],
    })
    proxy_registry.propose(proxy)
    with pytest.raises(EvalSchemaError, match="human approval"):
        proxy_registry.load_approved("dev-efficiency", "1")
    proxy_registry.approve("dev-efficiency", "1", reviewer="human")
    experiment = ControlledABExperiment(state, EventStore(config.paths))
    kwargs = {
        "adapter": adapter,
        "evaluator": PassingEvaluator(),
        "baseline_policy": AdapterPolicy(policy_id="baseline", budgets={"wall_timeout_seconds": 30}, settings={"workspace_isolated": True}),
        "candidate": candidate,
        "model": {"model_name_or_path": "fixture"},
        "source_overrides": {"owner/repo": source},
        "capability_protocol": protocol,
        "proxy_eval_ref": ("dev-efficiency", "1"),
    }
    development_kwargs = {key: value for key, value in kwargs.items()
                          if key not in {"capability_protocol", "proxy_eval_ref"}}
    development = experiment.run(
        EvalTaskSet("fixture-dev", "fixture", "test", tasks[:1]),
        experiment_id="dev-fixture", development_only=True, **development_kwargs,
    )
    assert development["track"] == "development"
    assert proxy.evaluate(development["comparison"]["task_results"])["status"] == "passed"
    report = experiment.run(task_set, experiment_id="final-fixture", **kwargs)
    assert report["capability"]["proxy_eval"]["status"] == "passed"
    assert report["capability"]["proxy_eval"]["objective_claim"] is False
    assert report["capability"]["terminal_selection"]["decision"] == "inconclusive"
    with pytest.raises(EvalSchemaError, match="already reserved"):
        experiment.run(task_set, experiment_id="second-fixture", **kwargs)
    resumed = experiment.run(task_set, experiment_id="final-fixture", resume=True, **kwargs)
    assert resumed["capability"]["protocol_digest"] == protocol.digest


def test_controlled_ab_injects_candidate_only_for_semantically_matching_tasks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    commit = _repo(source)
    repository = RepositorySpec(
        "owner/repo", commit, "https://invalid.example/owner/repo.git"
    )
    evaluation = SWEbenchEvaluationSpec("fixture", "test")
    task_set = EvalTaskSet(
        "semantic-held-out",
        "fixture",
        "test",
        (
            EvalTask("owner__repo-match", "Fix the parser regression", repository, evaluation),
            EvalTask("owner__repo-abstain", "Improve documentation headings", repository, evaluation),
        ),
    )
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    candidate = ContextCandidate(
        candidate_id="parser-context",
        version="1",
        title="Focus parser regressions",
        candidate_type="experience_activation",
        context_item={"content": "Start from one focused parser regression."},
        confidence=0.7,
        evidence_refs=("event:event-training",),
        expected_effect={"tool_calls": "decrease"},
        source_task_ids=("training-task",),
        applies_to={"repositories": ["owner/repo"], "task_signals": ["parser regression"]},
    )
    config = Config.load(tmp_path / "control")
    state = tmp_path / "state"
    store = EventStore(config.paths)

    report = ControlledABExperiment(state, store).run(
        task_set,
        adapter=FixtureAgentAdapter(
            artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)]
        ),
        evaluator=PassingEvaluator(),
        baseline_policy=AdapterPolicy(
            policy_id="baseline", settings={"workspace_isolated": True}
        ),
        candidate=candidate,
        model={"model_name_or_path": "fixture", "cost_tracking": "default"},
        experiment_id="semantic-activation-fixture",
        source_overrides={"owner/repo": source},
    )

    assert report["activation_gate"]["activated_count"] == 1
    assert report["activation_gate"]["abstained_count"] == 1
    assert report["comparison"]["totals"]["candidate_context_activated"] == 1
    assert report["comparison"]["totals"]["candidate_context_abstained"] == 1
    run_report = read_json(
        state / "eval" / "v2" / "runs" / report["candidate"]["eval_run_id"] / "report.json",
        {},
    )
    task_results = {item["task_id"]: item for item in run_report["tasks"]}
    assert task_results["owner__repo-match"]["context_activation"]["status"] == "activated"
    assert task_results["owner__repo-abstain"]["context_activation"]["status"] == "abstained"
    for task_id, expected_injections in (
        ("owner__repo-match", 1),
        ("owner__repo-abstain", 0),
    ):
        events = store.list_events(trace_id=task_results[task_id]["trace_id"])
        assert sum(event.type == "CONTEXT_ACTIVATION" for event in events) == 1
        assert sum(event.type == "CONTEXT_INJECT" for event in events) == expected_injections


def test_two_complete_context_policies_run_under_frozen_invariants(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _repo(source)
    task = EvalTask(
        task_id="owner__repo-context",
        instruction="Fix the issue",
        repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )
    task_set = EvalTaskSet("context-held-out", "fixture", "test", (task,))
    stage_budgets = tuple(
        StageBudget(stage, token_limit=1000, tool_call_limit=10, time_limit_seconds=60)
        for stage in ("exploration", "implementation", "verification")
    )
    common = {
        "status": "candidate",
        "source_rules": (ContextSourceRule("task_spec", ("exploration", "implementation", "verification")),),
        "stage_budgets": stage_budgets,
    }
    policy_a = ContextPolicy("resident-history", "1", history={"mode": "full"}, **common)
    policy_b = ContextPolicy("compact-history", "1", history={"mode": "compact", "compact_at_ratio": 0.8}, **common)
    config = Config.load(tmp_path / "control")
    state = tmp_path / "state"
    report = ContextPolicyAblation(state, EventStore(config.paths)).run(
        task_set,
        adapter=FixtureAgentAdapter(),
        evaluator=PassingEvaluator(),
        policy_a=policy_a,
        policy_b=policy_b,
        context_a=({"source": "task_spec", "content": "Fix the issue"},),
        context_b=({"source": "task_spec", "content": "Fix the issue"},),
        model={"model_name_or_path": "fixture", "cost_tracking": "default"},
        experiment_id="p1-context-ablation",
        source_overrides={"owner/repo": source},
    )
    assert report["invariant_check"]["valid"] is True
    assert report["baseline"]["policy_id"] == "resident-history"
    assert report["candidate"]["policy_id"] == "compact-history"
    assert (state / "eval" / "v2" / "context-ablations" / "p1-context-ablation" / "report.json").is_file()
