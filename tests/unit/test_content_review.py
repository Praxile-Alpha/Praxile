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


def test_function_submission_keeps_local_validation_authoritative():
    record, router, _ = run(response_mode="function")
    contract = router.chat.call_args.kwargs["response_contract"]
    assert contract["name"] == "submit_content_review_v1"
    items = contract["schema"]["properties"]["items"]
    assert items["minItems"] == items["maxItems"] == 1
    assert items["items"]["properties"]["id"]["enum"] == ["consent"]
    assert record["response_mode"] == "function"
    assert len(record["response_contract_sha256"]) == 64
    invalid, _, _ = run([{"id": "consent", "verdict": "preserved",
        "output_quote": "fabricated", "reason": "Present"}], response_mode="function")
    assert invalid["status"] == "abstained"
    assert invalid["validation_code"] == "unsupported_citation"


def test_default_mode_does_not_change_provider_protocol():
    record, router, _ = run()
    assert record["response_mode"] == "text"
    assert "response_contract" not in router.chat.call_args.kwargs
    assert "response_contract_sha256" not in record


def test_missing_items_has_safe_diagnostic():
    record, _, _ = run([])
    assert record["validation_code"] == "incomplete_coverage"


def test_explicit_checkpoint_precedes_validation_and_cannot_mutate_response():
    saved = []
    def checkpoint(payload):
        saved.append(dict(payload))
        payload["content"] = "altered"
    record, _, logger = run([], response_checkpoint=checkpoint)
    assert record["status"] == "abstained"
    assert record["validation_code"] == "incomplete_coverage"
    assert record["response_checkpoint_completed"] is True
    assert saved[0]["content"] == '{"items": []}'
    assert saved[0]["input_sha256"] == record["input_sha256"]
    assert saved[0]["response_sha256"] == record["response_sha256"]
    assert "content" not in record
    logger.add_model_cost.assert_called_once()


def test_checkpoint_failure_blocks_acceptance_without_retry_or_secret_leak():
    callback = Mock(side_effect=OSError("sensitive local path"))
    record, router, logger = run(response_checkpoint=callback)
    assert record["status"] == "abstained"
    assert record["failure_stage"] == "response_checkpoint"
    assert "items" not in record
    assert "sensitive" not in json.dumps(record)
    callback.assert_called_once()
    router.chat.assert_called_once()
    logger.add_model_cost.assert_called_once()


def test_tool_calls_finish_does_not_prove_complete_submission():
    router = Mock()
    router.chat.return_value = {"finish_reason": "tool_calls", "usage": {"completion_tokens": 3000},
        "content": json.dumps({"items": [{"id": "first", "verdict": "missing", "output_quote": ""}]})}
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    result = review_content(router, logger, source_text="source", output_text="output",
        requirements=[{"id": name, "requirement": "Preserve source", "source_quote": "source"}
                      for name in ("first", "second")], response_mode="function")
    assert result["status"] == "abstained"
    assert result["validation_code"] == "invalid_item_fields"
    assert result["expected_items"] == 2
    assert result["returned_items"] == 1
    assert result["output_budget_reached"] is True
    assert result["finish_reason"] == "tool_calls"
    assert "items" not in result


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


def test_function_submission_through_real_router_provider(tmp_path, monkeypatch):
    from praxile.config import Config, ProjectPaths
    from praxile.model import ModelRouter

    monkeypatch.setenv("PRAXILE_TEST_REVIEW_KEY", "test-only")
    config = Config({"model_providers": {"test": {"type": "openai_compatible",
        "base_url": "https://example.invalid/v1", "api_key_env": "PRAXILE_TEST_REVIEW_KEY",
        "models": [{"name": "test", "supports_tools": True}]}},
        "model_roles": {"content_review_judge": {"provider": "test", "model": "test"}}},
        ProjectPaths(tmp_path))
    router = ModelRouter(config)
    transport = router.providers["test"].transport
    arguments = json.dumps({"items": [{"id": "x", "verdict": "uncertain",
        "output_quote": "", "reason": "No equivalent evidence"}]})
    post = Mock(return_value={"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"type": "function", "function": {
            "name": "submit_content_review_v1", "arguments": arguments}}]}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30}})
    monkeypatch.setattr(transport, "post_json", post)
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    try:
        report = review_content(router, logger, source_text="source", output_text="output",
            requirements=[{"id": "x", "requirement": "x", "source_quote": "source"}],
            private=False, response_mode="function")
        assert report["status"] == "reviewed"
        assert report["all_declared_requirements_preserved"] is False
        assert report["finish_reason"] == "tool_calls"
        assert post.call_args.kwargs["payload"]["tool_choice"]["function"]["name"] == "submit_content_review_v1"
        post.assert_called_once()
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            close()
