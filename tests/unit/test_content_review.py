import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_review import review_content, _transport_failure
from praxile.model import ModelUnavailable


def run(items=None, **kwargs):
    items = items if items is not None else [
        {"id": "consent", "verdict": "preserved", "output_quote": "Obtain consent.",
         "reason": "The requirement is retained."}]
    router = Mock()
    router.chat.return_value = {"content": json.dumps({"items": items}),
                               "usage": {"prompt_tokens": 100, "completion_tokens": 40}}
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    record = review_content(router, logger, source_text="Obtain consent.",
        output_text="Obtain consent.", requirements=[{"id": "consent",
            "requirement": "Consent is required.", "source_quote": "Obtain consent."}], **kwargs)
    return record, router, logger


def test_anchored_review_is_not_verifier_or_promotion():
    record, router, logger = run()
    assert record["status"] == "reviewed"
    assert record["all_declared_requirements_preserved"] is True
    assert record["content_preservation_verified"] is False
    assert record["promotion_eligible"] is False
    assert record["requirement_coverage_verified"] is False
    assert record["official_evaluation"] is False
    assert record["usage_available"] is True
    assert record["logical_requests"] == 1
    assert len(record["response_sha256"]) == 64
    assert router.chat.call_args.kwargs["purpose"] == "content_review_judge"
    assert router.chat.call_args.kwargs["private"] is True
    logger.add_model_cost.assert_called_once()
    assert logger.data["content_reviews"] == [record]


@pytest.mark.parametrize("items", [
    [],
    [{"id": "unknown", "verdict": "missing", "output_quote": "", "reason": "Absent"}],
    [{"id": "consent", "verdict": "preserved", "output_quote": "fabricated", "reason": "Present"}],
    [{"id": "consent", "verdict": "preserved", "output_quote": "", "reason": "Present"}],
    [{"id": "consent", "verdict": [], "output_quote": "", "reason": "Present"}],
    [{"id": "consent", "verdict": "missing", "output_quote": "", "reason": "Absent"}] * 2,
])
def test_invalid_or_incomplete_review_abstains_and_accounts_usage(items):
    record, router, logger = run(items)
    assert record["status"] == "abstained"
    assert record["failure_stage"] == "validation"
    assert "items" not in record
    router.chat.assert_called_once()
    logger.add_model_cost.assert_called_once()


def test_uncertainty_is_not_success():
    record, _, _ = run([{"id": "consent", "verdict": "uncertain",
                         "output_quote": "", "reason": "Insufficient context"}])
    assert record["status"] == "reviewed"
    assert record["all_declared_requirements_preserved"] is False


def test_bad_source_citation_prevents_model_call():
    router = Mock()
    with pytest.raises(ValueError):
        review_content(router, Mock(), source_text="source", output_text="output",
            requirements=[{"id": "x", "requirement": "x", "source_quote": "invented"}])
    router.chat.assert_not_called()


def test_provider_failure_does_not_leak_exception_or_retry():
    router = Mock()
    router.chat.side_effect = ModelUnavailable("secret credential")
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    result = review_content(router, logger, source_text="source", output_text="output",
        requirements=[{"id": "x", "requirement": "x", "source_quote": "source"}])
    assert result["status"] == "abstained"
    assert result["failure_stage"] == "request"
    assert result["failure_category"] == "model_unavailable"
    assert result["logical_requests"] == 1
    assert result["usage_available"] is False
    assert result["transport_attempts_verified"] is False
    assert "secret" not in json.dumps(result)
    router.chat.assert_called_once()
    logger.add_model_cost.assert_not_called()


def test_transport_cause_projection_drops_secrets_and_limits_scope():
    cause = RuntimeError("secret provider body")
    cause.transport_evidence = {"attempt_count": 1, "secret": "credential", "attempts": [
        {"status": "http_error", "status_code": 429, "elapsed_ms": 12, "body": "secret"}]}
    wrapped = ModelUnavailable("secret URL")
    wrapped.__cause__ = cause
    report = _transport_failure(wrapped)
    assert report == {"scope": "nearest_transport_cause_only", "attempt_count": 1,
                      "attempts": [{"status": "http_error", "status_code": 429, "elapsed_ms": 12}],
                      "usage_unknown": True}
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("status,code,elapsed", [("secret", 429, 1), ("http_error", True, 1),
    ("timeout", None, -1), ("http_error", "secret", 1)])
def test_malformed_transport_diagnostics_are_not_persisted(status, code, elapsed):
    error = RuntimeError()
    error.transport_evidence = {"attempt_count": 1, "attempts": [
        {"status": status, "status_code": code, "elapsed_ms": elapsed}]}
    assert _transport_failure(error) is None


def test_cyclic_exception_chain_is_bounded():
    error = RuntimeError()
    error.__cause__ = error
    assert _transport_failure(error) is None


def test_real_router_and_provider_preserve_http_cause(tmp_path, monkeypatch):
    from praxile.config import Config, ProjectPaths
    from praxile.model import ModelRouter
    from praxile.http_transport import HTTPTransportError

    monkeypatch.setenv("PRAXILE_TEST_REVIEW_KEY", "test-only")
    config = Config({"model_providers": {"test": {"type": "openai_compatible",
        "base_url": "https://example.invalid/v1", "api_key_env": "PRAXILE_TEST_REVIEW_KEY"}},
        "model_roles": {"content_review_judge": {"provider": "test", "model": "test"}}},
        ProjectPaths(tmp_path))
    router = ModelRouter(config)
    error = HTTPTransportError("HTTP 429 sensitive-body", status_code=429)
    error.transport_evidence = {"attempt_count": 1, "attempts": [
        {"status": "http_error", "status_code": 429, "elapsed_ms": 10}]}
    transport = router.providers["test"].transport
    monkeypatch.setattr(transport, "post_json", Mock(side_effect=error))
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    try:
        report = review_content(router, logger, source_text="source", output_text="output",
            requirements=[{"id": "x", "requirement": "x", "source_quote": "source"}], private=False)
        assert report["status"] == "abstained"
        assert report["failure_category"] == "model_unavailable"
        assert report["transport_failure"]["attempts"][0]["status_code"] == 429
        assert "sensitive" not in json.dumps(report)
        assert report["usage_available"] is False
        transport.post_json.assert_called_once()
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            close()
