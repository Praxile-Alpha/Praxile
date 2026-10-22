from copy import deepcopy

import pytest

from praxile.eval.five_arm_usage import summarize_receipts


def receipt():
    return {"usage": {"schema_version": "praxile.broker_usage.v1", "admitted_calls": 1,
        "responses": 1, "failed_calls": 0, "inflight_calls": 0, "latency_seconds": 0.2,
        "known_tokens": {"input": 10, "output": 4}, "reported_calls": {"input": 1, "output": 1},
        "total_tokens": {"input": 10, "output": 4}}}


def test_missing_receipt_preserves_known_subtotal_without_fake_total():
    report = summarize_receipts([("executor", receipt()), ("executor", None), ("judge", receipt())])
    assert report["known_tokens"] == {"input": 20, "output": 8}
    assert report["total_tokens"] == {"input": None, "output": None}
    assert report["roles"]["executor"]["known_admitted_calls"] == 1
    assert report["roles"]["executor"]["admitted_calls"] is None
    assert report["roles"]["judge"]["admitted_calls"] == 1
    assert report["cost_usd"] is None


def test_failed_response_has_unknown_usage_not_zero():
    value = receipt()
    value["usage"].update(admitted_calls=2, failed_calls=1, total_tokens={"input": None, "output": None})
    report = summarize_receipts([("executor", value)])
    assert report["known_tokens"] == {"input": 10, "output": 4}
    assert report["total_tokens"] == {"input": None, "output": None}
    assert report["roles"]["executor"]["admitted_calls"] == 2


def test_explicit_zero_is_not_missing_and_empty_ledger_is_unknown():
    value = receipt()
    value["usage"].update(known_tokens={"input": 0, "output": 0}, total_tokens={"input": 0, "output": 0})
    assert summarize_receipts([("executor", value)])["total_tokens"] == {"input": 0, "output": 0}
    assert summarize_receipts([])["total_tokens"] == {"input": None, "output": None}


@pytest.mark.parametrize("field,value", [("admitted_calls", True), ("failed_calls", 2),
    ("responses", -1), ("latency_seconds", float("nan")), ("latency_seconds", -1),
    ("known_tokens", {"input": "10", "output": 4}), ("reported_calls", {"input": 0, "output": 1}),
    ("reported_calls", {"input": 2, "output": 1}), ("total_tokens", {"input": None, "output": 4})])
def test_inconsistent_or_invalid_usage_is_rejected(field, value):
    source = receipt()
    source["usage"][field] = value
    with pytest.raises(ValueError):
        summarize_receipts([("executor", source)])


def test_shared_quota_snapshot_is_not_counted_as_additional_usage():
    value = receipt()
    value["shared_budget"] = {"roles": {"executor": {"reserved_calls": 999}}}
    before = deepcopy(value)
    result = summarize_receipts([("executor", value), ("judge", value)])
    assert result["known_tokens"] == {"input": 20, "output": 8}
    assert value == before


def test_latency_overflow_is_rejected():
    value = receipt()
    value["usage"]["latency_seconds"] = 1e308
    with pytest.raises(ValueError, match="overflow"):
        summarize_receipts([("executor", value), ("executor", value)])
