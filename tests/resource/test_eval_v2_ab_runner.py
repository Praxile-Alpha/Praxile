from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter, FixtureArtifact, FixtureEvent
from praxile.config import Config
from praxile.eval.v2 import (
    ContextCandidate,
    ControlledABExperiment,
    EvalTask,
    EvalTaskSet,
    EvaluatorResult,
    RepositorySpec,
    PublicExperimentExporter,
    SWEbenchEvaluationSpec,
    SWEbenchPrediction,
)
from praxile.trace import AgentEvent, EventStore
from praxile.utils import utc_now


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
    )

    assert report["invariant_check"]["valid"] is True
    assert report["comparison"]["decision"] == "inconclusive"
    assert report["comparison"]["trace_overhead"]["context_inject_event_delta"] == 1
    assert report["diagnoses"]["baseline"][0]["outcome"] == "no_failure"
    assert report["diagnoses"]["candidate"][0]["attribution"]["abstained"] is True
    root = state / "eval" / "v2" / "experiments" / "p0-c-fixture"
    assert (root / "manifest.json").is_file()
    assert (root / "report.json").is_file()
    assert (root / "diagnoses" / "baseline" / "owner__repo-1.json").is_file()

    reanalyzed = experiment.analyze("p0-c-fixture")

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
    assert '"tokens": "<redacted:credential>"' not in metrics_text
    for line in trace_text.splitlines():
        event = AgentEvent.from_json(line)
        assert event.extensions["public_export"]["redacted"] is True
