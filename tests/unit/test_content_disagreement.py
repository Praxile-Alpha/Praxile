import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_disagreement import compare_judgments
from praxile.eval.content_review_batch import review_content_batched


def review(verdict="missing", budget=100, output="output", invalid=False):
    router = Mock()
    router.chat.return_value = {"content": json.dumps({"items": [] if invalid else [
        {"id": "r", "verdict": verdict, "reason": "PRIVATE", "output_quote":
         output if verdict in ("preserved", "contradicted") else ""}]}), "usage": {}}
    return review_content_batched(router, SimpleNamespace(data={}, add_model_cost=Mock()),
        source_text="source", output_text=output, requirements=[
            {"id": "r", "requirement": "Keep source", "source_quote": "source"}],
        total_output_tokens=budget)


def compare(left, right):
    return compare_judgments(left, right, left_judge_sha256="a" * 64, right_judge_sha256="b" * 64)


def test_disagreement_requires_review_without_choosing_winner():
    result = compare(review(), review("preserved", budget=200))
    assert result["status"] == "triaged"
    assert result["disagreement_count"] == 1
    assert result["review_required_ids"] == ["r"]
    assert result["rows"][0]["reference_verdict"] is None
    assert {row["field"] for row in result["condition_differences"]} == {"groups", "total_output_tokens"}
    assert result["winner"] is None
    assert result["promotion_eligible"] is result["semantic_accuracy_verified"] is False
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("verdict,needs_review", [("preserved", False), ("uncertain", True)])
def test_agreement_does_not_become_reference_truth(verdict, needs_review):
    result = compare(review(verdict), review(verdict))
    assert result["disagreement_count"] == 0
    assert result["rows"][0]["needs_human_review"] is needs_review
    assert result["rows"][0]["reference_verdict"] is None
    assert result["reference_labels_verified"] is False


def test_different_artifact_cannot_be_treated_as_judge_disagreement():
    result = compare(review(), review(output="changed"))
    assert result["reason"] == "snapshot_mismatch"
    assert "rows" not in result


def test_incomplete_review_is_not_a_negative_vote():
    result = compare(review(), review(invalid=True))
    assert result["reason"] == "incomplete_review"
    assert "rows" not in result


def test_invalid_identity_or_modified_plan_is_rejected():
    with pytest.raises(ValueError):
        compare_judgments(review(), review(), left_judge_sha256="bad", right_judge_sha256="a" * 64)
    changed = review()
    changed["plan"]["source_text_sha256"] = "c" * 64
    with pytest.raises(ValueError):
        compare(review(), changed)


def test_public_disagreement_matches_both_published_judgments():
    root = Path(__file__).resolve().parents[2] / "docs/results"
    report = json.loads((root / "judge-disagreement-20261021.json").read_text())
    minimax = json.loads((root / "docx-review-allocation-20261021.json").read_text())
    deepseek = json.loads((root / "deepseek-content-review-20261021.json").read_text())
    left = {item["id"]: item["verdict"] for item in minimax["report"]["items"]}
    right = {item["id"]: item["candidate"] for item in deepseek["comparison"]["rows"]}
    assert report["requirement_count"] == len(left) == len(right) == 16
    assert report["disagreement_count"] == 4
    for row in report["rows"]:
        assert (row["left"], row["right"]) == (left[row["id"]], right[row["id"]])
        assert row["reference_verdict"] is None
    assert report["review_required_ids"] == ["packet-row-6", "packet-row-14", "roles", "dates"]
    assert report["local_file_consistency_checked"] == {"left": False, "right": True}
    assert report["winner"] is None and report["promotion_eligible"] is False
