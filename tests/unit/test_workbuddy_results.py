import json

import pytest

from praxile.eval.workbuddy_results import import_trial
from praxile.eval.v2.schema import EvalSchemaError


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def trial(tmp_path):
    result = {"task_name": "workbuddy/example", "task_checksum": "frozen",
              "finished_at": "2026-10-19T00:00:00Z", "agent_execution": {"finished_at": "done"},
              "config": {"verifier": {"import_path": "workbuddy_bench.judge:CompositeVerifier"}},
              "verifier_result": {"rewards": {"reward": 0.5}}}
    write(tmp_path, "result.json", result)
    write(tmp_path, "verifier/reward.json", {"reward": 0.5})
    write(tmp_path, "verifier/score.json", {"reward": 0.5, "test_status": "partial_pass",
        "judges": [], "verdicts": [], "diagnostics": {"private": "hidden rubric"}})
    return tmp_path


def load(root):
    return import_trial("example", root, expected_task_checksum="frozen")


def change(root, file, **fields):
    value = json.loads((root / file).read_text())
    value.update(fields)
    write(root, file, value)


def test_partial_reward_preserves_private_details_and_unknown_usage(trial):
    report = load(trial)
    assert report["outcome"]["status"] == "completed"
    assert report["outcome"]["scores"][0]["value"] == 0.5
    assert report["usage"] is None
    assert report["verifier_environment_mode"] == "unknown"
    assert report["official_details"]["diagnostics"]["private"] == "hidden rubric"
    assert len(report["evidence_digests"]) == 3


@pytest.mark.parametrize("test_status,status", [("no_pass", "completed"),
    ("judge_error", "judge_unavailable"), ("build_error", "infra_error")])
def test_zero_is_not_always_valid_evaluation(trial, test_status, status):
    change(trial, "verifier/score.json", reward=0, test_status=test_status)
    change(trial, "verifier/reward.json", reward=0)
    change(trial, "result.json", verifier_result={"rewards": {"reward": 0}})
    report = load(trial)
    assert report["outcome"]["status"] == status
    assert bool(report["outcome"]["scores"]) is (status == "completed")


def test_missing_verifier_and_timeout_remain_incomplete(trial):
    (trial / "verifier/score.json").unlink()
    assert load(trial)["outcome"]["status"] == "infra_error"
    change(trial, "result.json", exception_info={"exception_type": "VerifierTimeoutError"})
    assert load(trial)["outcome"]["status"] == "timed_out"


@pytest.mark.parametrize("file,fields", [
    ("result.json", {"task_checksum": "different"}),
    ("result.json", {"config": {"verifier": {}}}),
    ("result.json", {"finished_at": None}),
    ("verifier/reward.json", {"reward": float("nan")}),
    ("verifier/reward.json", {"reward": True}),
    ("verifier/reward.json", {"reward": 0.7}),
    ("verifier/score.json", {"test_status": "full_pass"}),
    ("verifier/score.json", {"judges": None}),
])
def test_rejects_inconsistent_evidence(trial, file, fields):
    change(trial, file, **fields)
    with pytest.raises(EvalSchemaError):
        load(trial)


def test_rejects_symlink_evidence(trial):
    path = trial / "verifier/score.json"
    path.unlink()
    path.symlink_to(trial / "verifier/reward.json")
    with pytest.raises(EvalSchemaError, match="symlinks"):
        load(trial)


def test_nonfatal_judge_issues_remain_visible(trial):
    change(trial, "verifier/score.json", judges=[{"status": "abstain", "judge_type": "llm"}])
    report = load(trial)
    assert report["outcome"]["status"] == "completed"
    assert report["judge_issues"][0]["status"] == "abstain"


def test_official_artifact_writer_compatibility(trial):
    pytest.importorskip("workbuddy_bench.judge.core.artifacts")
    from workbuddy_bench.judge.core.artifacts import ArtifactWriter
    from workbuddy_bench.judge.core.models import ScoreResult
    score = ScoreResult(reward=0.5, test_status="partial_pass", tests_passed=1, tests_total=2)
    ArtifactWriter(reward_json_path=trial / "verifier/reward.json",
                   score_json_path=trial / "verifier/score.json").write(score)
    change(trial, "result.json", verifier_result={"rewards": score.reward_payload()})
    assert load(trial)["official_details"]["tests_total"] == 2
