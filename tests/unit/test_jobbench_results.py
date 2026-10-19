import json

import pytest

from praxile.eval.jobbench_results import import_trial
from praxile.eval.v2.schema import EvalSchemaError


TASK = "easy--writer--task1"
CHECKSUM = "a" * 64


def write(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data))


def trial(root, passed=True):
    reward = round(2 / 3, 4) if passed else 0
    write(root, "result.json", {"task_name": f"jobbench/{TASK}", "task_checksum": CHECKSUM,
        "finished_at": "2026-10-19T00:00:00Z", "verifier_environment_mode": "separate",
        "agent_execution": {"finished_at": "2026-10-19T00:00:00Z"},
        "verifier_result": {"rewards": {"reward": reward}}})
    write(root, "verifier/verification.json", {"status": "ok", "reward": reward})
    write(root, "verifier/reward.json", {"reward": reward})
    write(root, "verifier/judge-details.json", {"judge_model": "independent-judge", "total_score": 2 if passed else 0,
        "max_score": 3, "total_count": 2, "rubrics": [
            {"index": 0, "weight": 2, "result": {"passed": passed, "score": 2 if passed else 0}},
            {"index": 1, "weight": 1, "result": {"passed": False, "score": 0}},
        ]})


@pytest.mark.parametrize("passed", [True, False])
def test_preserves_official_weighted_denominator_and_zero(tmp_path, passed):
    trial(tmp_path, passed)
    report = import_trial(TASK, tmp_path, expected_task_checksum=CHECKSUM, expected_rubric_weights=(2, 1))
    assert report["outcome"]["status"] == "completed"
    assert report["outcome"]["scores"][0]["maximum"] == 3
    assert report["outcome"]["scores"][0]["value"] == (2 if passed else 0)
    assert report["usage"] is None
    assert len(report["evidence_digests"]) == 4
    assert report["official_details"]["judge_model"] == "independent-judge"


def test_verifier_error_does_not_become_zero(tmp_path):
    trial(tmp_path)
    write(tmp_path, "verifier/verification.json", {"status": "error", "error": "judge unavailable"})
    report = import_trial(TASK, tmp_path, expected_task_checksum=CHECKSUM, expected_rubric_weights=(2, 1))
    assert report["outcome"]["status"] == "infra_error"
    assert report["outcome"]["scores"] == []


def test_missing_verification_and_timeout(tmp_path):
    write(tmp_path, "result.json", {"task_name": f"jobbench/{TASK}", "task_checksum": CHECKSUM,
                                   "exception_info": {"exception_type": "VerifierTimeoutError"}})
    report = import_trial(TASK, tmp_path, expected_task_checksum=CHECKSUM, expected_rubric_weights=(2, 1))
    assert report["outcome"]["status"] == "timed_out"
    assert report["usage"] is None


@pytest.mark.parametrize("mutation", ["reward", "denominator", "duplicate", "error", "identity", "checksum", "shared", "symlink"])
def test_rejects_inconsistent_or_mismatched_evidence(tmp_path, mutation):
    trial(tmp_path)
    if mutation == "reward":
        write(tmp_path, "verifier/reward.json", {"reward": 1})
    elif mutation in {"denominator", "duplicate", "error"}:
        path = "verifier/judge-details.json"
        data = json.loads((tmp_path / path).read_text())
        if mutation == "denominator":
            data["max_score"] = 1
        elif mutation == "duplicate":
            data["rubrics"][1]["index"] = 0
        else:
            data["error"] = "extraction failed"
        write(tmp_path, path, data)
    elif mutation == "symlink":
        (tmp_path / "verifier/reward.json").unlink()
        (tmp_path / "verifier/reward.json").symlink_to(tmp_path / "verifier/verification.json")
    else:
        data = json.loads((tmp_path / "result.json").read_text())
        field = {"identity": "task_name", "checksum": "task_checksum", "shared": "verifier_environment_mode"}[mutation]
        data[field] = "wrong"
        write(tmp_path, "result.json", data)
    with pytest.raises(EvalSchemaError):
        import_trial(TASK, tmp_path, expected_task_checksum=CHECKSUM, expected_rubric_weights=(2, 1))


def test_self_consistent_report_cannot_change_frozen_weights(tmp_path):
    trial(tmp_path)
    with pytest.raises(EvalSchemaError, match="frozen official"):
        import_trial(TASK, tmp_path, expected_task_checksum=CHECKSUM, expected_rubric_weights=(1, 2))
