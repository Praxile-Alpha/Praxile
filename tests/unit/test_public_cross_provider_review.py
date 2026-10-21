"""Receipt consistency, not independent verification of judge accuracy."""
import json
from pathlib import Path


def test_cross_provider_receipt_retains_failures_and_no_promotion():
    root = Path(__file__).resolve().parents[2] / "docs/results"
    receipt = json.loads((root / "deepseek-content-review-20261021.json").read_text())
    assert receipt["experiment"]["executor"] == "MiniMax-M3.1-Flash"
    assert receipt["experiment"]["judge"] == "deepseek-flash"
    assert receipt["experiment"]["model_alias_is_mutable"] is True
    comparison = receipt["comparison"]
    assert comparison["status"] == "compared"
    assert comparison["local_file_consistency_checked"] is True
    assert comparison["semantic_accuracy_verified"] is False
    assert comparison["promotion_eligible"] is receipt["promotion_eligible"] is False
    assert len(comparison["rows"]) == comparison["requirement_count"] == 16
    assert len(receipt["arms"]) == len(receipt["initial_protocol_attempts"]) == 2
    requests = sum(arm["usage_evidence"]["calls"] for arm in receipt["arms"])
    for arm in receipt["arms"]:
        assert arm["judge_identity"]["provider"] == "deepseek"
        assert arm["status"] == "reviewed"
        assert arm["usage_evidence"]["recorded_calls"] == 4
    for arm in receipt["initial_protocol_attempts"]:
        assert arm["status"] == "abstained"
        assert arm["transport_failure"]["usage_unknown"] is True
        requests += arm["logical_requests"]
    requests += receipt["protocol_diagnostic"]["requests"]
    assert requests == receipt["total_post_requests"] == 11
    assert receipt["total_cost_usd"] is None
