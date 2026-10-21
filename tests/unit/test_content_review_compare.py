import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_review_batch import review_content_batched
from praxile.eval.content_review_compare import compare_content_reviews


def report(output="output", budget=100, invalid=False):
    router = Mock()
    router.chat.return_value = {"content": '{"items": []}' if invalid else json.dumps({"items": [
        {"id": "r", "verdict": "missing", "output_quote": "", "reason": "Absent"}]}), "usage": {}}
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    return review_content_batched(router, logger, source_text="source", output_text=output,
        requirements=[{"id": "r", "requirement": "Keep source", "source_quote": "source"}],
        total_output_tokens=budget)


def compare(left, right, other="a" * 64):
    return compare_content_reviews(left, right, baseline_judge_sha256="a" * 64, candidate_judge_sha256=other)


def test_complete_same_conditions_produce_descriptive_rows_not_promotion():
    result = compare(report(), report("different output"))
    assert result["status"] == "compared"
    assert result["rows"] == [{"id": "r", "baseline": "missing", "candidate": "missing"}]
    assert result["promotion_eligible"] is False
    assert result["provenance_verified"] is False


def test_incomplete_arm_does_not_disappear():
    assert compare(report(), report(invalid=True))["reason"] == "incomplete_review"


def test_budget_or_judge_mismatch_is_not_comparable():
    assert compare(report(), report(budget=200))["reason"] == "conditions_mismatch"
    assert compare(report(), report(), "b" * 64)["reason"] == "conditions_mismatch"


@pytest.mark.parametrize("mutation", ["digest", "items", "group_output", "coverage"])
def test_inconsistent_evidence_is_rejected(mutation):
    left = report()
    right = copy.deepcopy(left)
    if mutation == "digest":
        right["plan"]["total_output_tokens"] += 1
    elif mutation == "items":
        right["items"] = []
    elif mutation == "group_output":
        right["groups"][0]["output_text_sha256"] = "b" * 64
    else:
        right["unreviewed_ids"] = ["r"]
    with pytest.raises(ValueError):
        compare(left, right)
