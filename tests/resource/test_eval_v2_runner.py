from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter, FixtureArtifact, FixtureEvent
from praxile.config import Config
from praxile.eval.v2 import (
    BenchmarkEvalRunner,
    EvalTask,
    EvalTaskSet,
    EvaluatorResult,
    RepositorySpec,
    SWEbenchEvaluationSpec,
    SWEbenchPrediction,
)
from praxile.trace import EventStore
from praxile.utils import utc_now


pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


class RecordingEvaluator:
    name = "recording"
    version = "1"

    def __init__(self) -> None:
        self.calls = 0

    def identity(self) -> Mapping[str, Any]:
        return {"name": self.name, "version": self.version}

    def evaluate(self, task: EvalTask, prediction: SWEbenchPrediction, output_root: Path) -> EvaluatorResult:
        self.calls += 1
        output_root.mkdir(parents=True, exist_ok=True)
        evidence = output_root / "result.json"
        evidence.write_text('{"resolved": true}\n', encoding="utf-8")
        now = utc_now()
        return EvaluatorResult("completed", True, now, now, (str(evidence),), {})


def _source_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Praxile Tests"], cwd=path, check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "candidate.patch").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def test_benchmark_runner_persists_trace_metrics_manifest_and_resumes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _source_repo(source)
    state_root = tmp_path / "control-state"
    config = Config.load(tmp_path / "control")
    event_store = EventStore(config.paths)
    task = EvalTask(
        task_id="owner__repo-1",
        instruction="Fix the issue",
        repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("SWE-bench/SWE-bench_Lite", "test"),
    )
    task_set = EvalTaskSet("fixed-dev", "SWE-bench/SWE-bench_Lite", "test", (task,))
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    adapter = FixtureAgentAdapter(
        events=[
            FixtureEvent("MODEL_CALL", payload={"retry": True}),
            FixtureEvent("TOOL_CALL", payload={"tool": "edit"}),
            FixtureEvent("VERIFICATION", payload={"status": "failed"}),
            FixtureEvent("VERIFICATION", payload={"status": "passed"}),
        ],
        artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)],
    )
    evaluator = RecordingEvaluator()
    runner = BenchmarkEvalRunner(state_root, event_store)
    policy = AdapterPolicy(policy_id="baseline", budgets={"wall_timeout_seconds": 30})

    report = runner.run(
        task_set,
        adapter=adapter,
        evaluator=evaluator,
        policy=policy,
        model={"model_name_or_path": "fixture-model"},
        eval_run_id="fixed-run",
        source_overrides={"owner/repo": source},
    )
    resumed = runner.run(
        task_set,
        adapter=adapter,
        evaluator=evaluator,
        policy=policy,
        model={"model_name_or_path": "fixture-model"},
        eval_run_id="fixed-run",
        resume=True,
        source_overrides={"owner/repo": source},
    )

    assert report["report_type"] == "baseline"
    assert report["metrics"]["resolved_count"] == 1
    assert report["metrics"]["tool_calls"] == 1
    assert report["metrics"]["retries"] == 1
    assert report["metrics"]["recoveries"] == 1
    assert report["tasks"][0]["trace_id"]
    assert event_store.list_events(trace_id=report["tasks"][0]["trace_id"])
    assert report["tasks"][0]["artifacts"]
    assert "evaluator_report" in {item["type"] for item in report["tasks"][0]["artifacts"]}
    preserved_uri = report["tasks"][0]["artifacts"][0]["uri"]
    assert Path(unquote(urlparse(preserved_uri).path)).is_file()
    assert any(
        item.metadata.get("materialized_by") == "praxile.eval.v2"
        for item in event_store.list_artifacts(report["tasks"][0]["trace_id"])
    )
    evaluator_event = next(
        event
        for event in event_store.list_events(trace_id=report["tasks"][0]["trace_id"])
        if event.actor.startswith("praxile-evaluator:")
    )
    assert evaluator_event.type == "VERIFICATION"
    assert evaluator_event.payload["resolved"] is True
    assert evaluator_event.artifact_ids
    assert not Path(report["tasks"][0]["repository"]["workspace_root"]).exists()
    assert evaluator.calls == 1
    assert resumed["tasks"][0]["prediction_digest"] == report["tasks"][0]["prediction_digest"]
    assert (state_root / "eval" / "v2" / "runs" / "fixed-run" / "manifest.json").is_file()


def test_benchmark_runner_isolates_task_errors(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    config = Config.load(tmp_path / "control")
    missing_task = EvalTask(
        task_id="missing__repo-1",
        instruction="Fix it",
        repository=RepositorySpec("missing/repo", "a" * 40, "https://invalid.example/repo.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )
    source = tmp_path / "source"
    commit = _source_repo(source)
    valid_task = EvalTask(
        task_id="valid__repo-2",
        instruction="Fix it",
        repository=RepositorySpec("valid/repo", commit, "https://invalid.example/valid.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    runner = BenchmarkEvalRunner(state_root, EventStore(config.paths))
    evaluator = RecordingEvaluator()

    report = runner.run(
        EvalTaskSet("errors", "fixture", "test", (missing_task, valid_task)),
        adapter=FixtureAgentAdapter(artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)]),
        evaluator=evaluator,
        policy=AdapterPolicy(),
        model={"model_name_or_path": "fixture"},
        eval_run_id="error-run",
        source_overrides={"missing/repo": tmp_path / "does-not-exist", "valid/repo": source},
    )

    assert report["tasks"][0]["status"] == "error"
    assert report["tasks"][0]["error"]["type"] in {"RuntimeError", "FileNotFoundError"}
    assert report["metrics"]["unknown_evaluation_count"] == 1
    assert report["tasks"][1]["status"] == "completed"
    assert report["tasks"][1]["evaluator"]["resolved"] is True
    assert evaluator.calls == 1


def test_benchmark_runner_preserves_agent_evidence_when_patch_is_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _source_repo(source)
    config = Config.load(tmp_path / "control")
    task = EvalTask(
        task_id="owner__repo-no-patch",
        instruction="Investigate the issue",
        repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )
    digest = "sha256:" + hashlib.sha256((source / "app.py").read_bytes()).hexdigest()
    runner = BenchmarkEvalRunner(tmp_path / "state", EventStore(config.paths))

    report = runner.run(
        EvalTaskSet("missing-patch", "fixture", "test", (task,)),
        adapter=FixtureAgentAdapter(
            artifacts=[FixtureArtifact("process_stdout", "app.py", digest)]
        ),
        evaluator=RecordingEvaluator(),
        policy=AdapterPolicy(),
        model={"model_name_or_path": "fixture"},
        eval_run_id="missing-patch-run",
        source_overrides={"owner/repo": source},
    )

    result = report["tasks"][0]
    assert result["status"] == "error"
    assert "did not produce a patch artifact" in result["error"]["message"]
    assert len(result["artifacts"]) == 1
    preserved = Path(unquote(urlparse(result["artifacts"][0]["uri"]).path))
    assert preserved.is_file()
    assert not Path(result["repository"]["workspace_root"]).exists()
