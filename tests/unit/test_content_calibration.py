import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_calibration import calibrate_content_review


def cases():
    return [{"id": "case-private-label", "source_text": "Approval required.",
        "output_text": "Approval not required.", "requirements": [{"id": "r",
        "requirement": "Retain approval condition", "source_quote": "Approval required."}],
        "expected": {"r": "contradicted"}}]


def invoke(router, logger, suite=None):
    return calibrate_content_review(router, logger, cases=cases() if suite is None else suite,
        max_tokens_per_case=300, response_mode="text", citation_mode="quote")


@pytest.mark.parametrize("verdict", ["preserved", "contradicted", "uncertain"])
def test_reference_labels_are_not_sent_and_metrics_are_not_protocol_success(verdict):
    router = Mock()
    router.chat.return_value = {"content": json.dumps({"items": [{"id": "r", "verdict": verdict,
        "output_quote": "Approval not required.", "reason": "Comparison"}]}), "usage": {}}
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    result = invoke(router, logger)
    payload = json.loads(router.chat.call_args.args[0][1]["content"])
    assert set(payload) == {"source_text", "output_text", "requirements"}
    assert "case-private-label" not in str(router.chat.call_args)
    assert result["correct_count"] == int(verdict == "contradicted")
    assert result["abstention_count"] == int(verdict == "uncertain")
    assert result["false_preserved_count"] == int(verdict == "preserved")
    assert result["promotion_eligible"] is False
    assert result["probability_calibration_error"] is None


def test_late_invalid_reference_label_prevents_all_calls():
    router = Mock()
    suite = cases() + [{**cases()[0], "id": "second", "expected": {"unknown": "preserved"}}]
    with pytest.raises(ValueError):
        invoke(router, SimpleNamespace(data={}), suite)
    router.chat.assert_not_called()


def test_invalid_response_is_abstention_not_silent_case_drop():
    router = Mock()
    router.chat.return_value = {"content": "broken", "usage": {}}
    result = invoke(router, SimpleNamespace(data={}, add_model_cost=Mock()))
    assert result["requirement_count"] == result["abstention_count"] == 1
    assert result["conditional_accuracy"] is None
    assert result["accuracy_including_abstention"] == 0


def test_public_smoke_report_is_bound_to_authored_suite_not_general_calibration():
    root = Path(__file__).resolve().parents[2]
    suite = json.loads((root / "tests/fixtures/content_review_development.json").read_text())
    receipt = json.loads((root / "docs/results/content-calibration-smoke-20261021.json").read_text())
    report = receipt["report"]
    assert report["suite_sha256"] == hashlib.sha256(json.dumps(suite["cases"], sort_keys=True).encode()).hexdigest()
    expected = {(case["id"], key): value for case in suite["cases"] for key, value in case["expected"].items()}
    assert {(r["case_id"], r["requirement_id"]): r["expected"] for r in report["rows"]} == expected
    assert report["case_count"] == 4
    assert report["reference_labels_verified"] is False
    assert report["probability_calibration_error"] is None
    assert report["promotion_eligible"] is False
