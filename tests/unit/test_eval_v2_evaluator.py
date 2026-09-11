from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from praxile.adapters import AdapterRunResult
from praxile.eval.v2 import (
    EvalTask,
    OfficialSWEbenchEvaluator,
    RepositorySpec,
    SWEbenchEvaluationSpec,
    SWEbenchPrediction,
    prediction_from_adapter_result,
)
from praxile.trace import ArtifactRecord, RunHandle


def _task() -> EvalTask:
    return EvalTask(
        task_id="owner__repo-1",
        instruction="Fix the failing parser",
        repository=RepositorySpec("owner/repo", "a" * 40, "https://github.com/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("SWE-bench/SWE-bench_Lite", "test"),
    )


def test_prediction_handoff_uses_official_field_names(tmp_path: Path) -> None:
    patch = tmp_path / "candidate.patch"
    patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(patch.read_bytes()).hexdigest()
    result = AdapterRunResult(
        handle=RunHandle("fixture", "native", "trace", "run", _task().task_id),
        events=(),
        artifacts=(
            ArtifactRecord(
                artifact_id="artifact",
                trace_id="trace",
                run_id="run",
                type="workspace_patch",
                uri="candidate.patch",
                content_digest=digest,
                producer_event_id="event",
                created_at="2026-01-01T00:00:00+00:00",
            ),
        ),
    )

    prediction = prediction_from_adapter_result(
        _task(), result, workspace_root=tmp_path, model_name_or_path="model/x"
    )

    assert set(prediction.to_dict()) == {"instance_id", "model_name_or_path", "model_patch"}
    assert prediction.model_patch.startswith("diff --git")


def test_official_evaluator_invokes_one_instance_and_reads_report(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        (cwd / "report.json").write_text(json.dumps({"resolved_ids": [_task().task_id]}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    evaluator = OfficialSWEbenchEvaluator(process_runner=fake_runner, timeout_seconds=20)
    result = evaluator.evaluate(
        _task(),
        SWEbenchPrediction(_task().task_id, "fixture-model", "diff --git a/a b/a\n"),
        tmp_path,
    )

    assert result.status == "completed"
    assert result.resolved is True
    assert "--instance_ids" in captured[0]
    assert captured[0][captured[0].index("--instance_ids") + 1] == _task().task_id
    handoff = json.loads((tmp_path / "prediction.jsonl").read_text(encoding="utf-8"))
    assert handoff["model_name_or_path"] == "fixture-model"


def test_official_evaluator_turns_process_timeout_into_task_result(tmp_path: Path) -> None:
    def timeout_runner(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout, output="partial", stderr="late")

    result = OfficialSWEbenchEvaluator(process_runner=timeout_runner, timeout_seconds=2).evaluate(
        _task(), SWEbenchPrediction(_task().task_id, "fixture-model", "patch"), tmp_path
    )

    assert result.status == "timed_out"
    assert result.resolved is None
    assert (tmp_path / "evaluator.stdout.log").read_text(encoding="utf-8") == "partial"
