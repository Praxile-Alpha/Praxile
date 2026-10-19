import json
from dataclasses import replace

import pytest

from praxile.eval.contracts import (
    BenchmarkOutcome, BenchmarkScore, BenchmarkTask, EnvironmentSpec,
    EvaluatorReference, InputArtifact, OutputContract,
)
from praxile.eval.v2.schema import EvalSchemaError, EvalTask, RepositorySpec, SWEbenchEvaluationSpec


def task():
    return BenchmarkTask(
        "office-1", "workbuddy", "revision-1", "office", "Produce a totals spreadsheet",
        EnvironmentSpec("harbor", "private-task-location", "image-revision"),
        EvaluatorReference("official", "1", "private-rubric-location", "sha256:" + "a" * 64),
        inputs=(InputArtifact("source.csv", "private-download-location", "sha256:" + "b" * 64),),
        outputs=(OutputContract("result.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),),
    )


def test_non_git_roundtrip_and_allowlisted_agent_projection():
    original = task()
    restored = BenchmarkTask.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original
    assert restored.fingerprint == original.fingerprint
    view = restored.to_adapter_task("/workspace", trace_id="trace", run_id="run").to_dict()
    assert "private-" not in json.dumps(view)
    assert "evaluator" not in view["metadata"]
    assert view["metadata"]["outputs"][0]["path"] == "result.xlsx"
    changed = replace(original, evaluator=replace(original.evaluator, content_digest="sha256:" + "c" * 64))
    assert changed.fingerprint != original.fingerprint


@pytest.mark.parametrize("path", ["../rubric", "/etc/passwd", "C:/secret", "a\\b", "a/../b", "a//b", ".", "a\x00b"])
def test_artifact_paths_reject_escape_and_ambiguous_names(path):
    with pytest.raises(EvalSchemaError):
        OutputContract(path, "text/plain")


def test_reject_unknown_fields_and_mutable_or_duplicate_inputs():
    data = task().to_dict()
    data["rubric"] = "hidden"
    with pytest.raises(EvalSchemaError):
        BenchmarkTask.from_dict(data)
    with pytest.raises(EvalSchemaError):
        replace(task(), inputs=list(task().inputs))
    with pytest.raises(EvalSchemaError):
        replace(task(), inputs=task().inputs * 2)
    with pytest.raises(EvalSchemaError):
        replace(task(), outputs=(OutputContract("source.csv", "text/csv"),))


def test_legacy_bridge_preserves_original_fingerprint_and_private_boundary():
    legacy = EvalTask(
        "sympy__sympy-1", "Repair parser", RepositorySpec("sympy/sympy", "a" * 40, "https://example.org/repo.git"),
        SWEbenchEvaluationSpec("swe-bench", "test", test_patch="secret-tests", reference_patch="secret-answer"),
        metadata={"private_hint": "never forward arbitrary metadata"},
    )
    before, fingerprint = legacy.to_dict(), legacy.fingerprint
    converted = BenchmarkTask.from_swebench(legacy, dataset_revision="revision", private_ref="private:task")
    assert legacy.to_dict() == before
    assert legacy.fingerprint == fingerprint
    assert EvalTask.from_dict(before).fingerprint == fingerprint
    view = converted.to_adapter_task("/workspace", trace_id="t", run_id="r").to_dict()
    assert "secret" not in json.dumps(view)
    assert "private_hint" not in json.dumps(view)
    assert converted.environment.kind == "git_worktree"


def test_preserve_official_score_denominator_and_missing_judge_state():
    outcome = BenchmarkOutcome("job-1", "completed", (BenchmarkScore("weighted_rubric", 7, 12),), ("official-report.json",))
    assert BenchmarkOutcome.from_dict(outcome.to_dict()) == outcome
    assert outcome.scores[0].maximum == 12
    missing = BenchmarkOutcome("job-1", "judge_unavailable")
    assert missing.to_dict()["scores"] == []
    with pytest.raises(EvalSchemaError):
        replace(missing, scores=outcome.scores)


@pytest.mark.parametrize("value,maximum", [(float("nan"), 1), (1, float("inf")), (True, 1), (2, 1), (0, 0)])
def test_reject_invalid_scores(value, maximum):
    with pytest.raises(EvalSchemaError):
        BenchmarkScore("score", value, maximum)
