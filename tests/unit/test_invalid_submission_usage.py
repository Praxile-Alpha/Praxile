from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config, ProjectPaths
from praxile.eval.content_review import review_content
from praxile.model import InvalidStructuredResponse, ModelError, ModelRouter
from praxile.trajectory import TrajectoryLogger


@pytest.mark.parametrize("message,code", [
    ({"content": "sensitive body"}, "submission_count_or_shape"),
    ({"tool_calls": [{"type": "function", "function": {
        "name": "private_name", "arguments": "{}"}}]}, "submission_function_mismatch"),
    ({"tool_calls": [{"type": "function", "function": {
        "name": "submit_content_review_v1", "arguments": '{"private":'}}]}, "submission_arguments_invalid"),
])
def test_invalid_received_submission_records_usage_once(tmp_path, message, code):
    config = Config({"model_providers": {"fixture": {"type": "openai_compatible",
        "base_url": "http://localhost", "models": [{"name": "fixture", "supports_tools": True}]}},
        "model_roles": {"content_review_judge": {"provider": "fixture", "model": "fixture"}}},
        ProjectPaths(tmp_path))
    router = ModelRouter(config)
    original = router.providers["fixture"].transport
    close = getattr(original, "close", None)
    if callable(close):
        close()
    transport = SimpleNamespace(post_json=Mock(return_value={"choices": [
        {"message": message, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 20, "secret": "private_usage"}}))
    router.providers["fixture"].transport = transport
    logger = TrajectoryLogger("Invalid submission test", {})
    record = review_content(router, logger, source_text="source", output_text="output",
        requirements=[{"id": "r", "source_quote": "source", "requirement": "Preserve"}],
        private=False, response_mode="function")
    assert record["status"] == "abstained"
    assert record["submission_error_code"] == code
    assert record["usage_available"] is True
    assert record["finish_reason"] == "tool_calls"
    calls = logger.data["model_routing"]["calls"]
    assert len(calls) == 1 and calls[0]["status"] == "failure"
    assert calls[0]["usage"] == {"prompt_tokens": 12, "completion_tokens": 20}
    assert logger.data["cost"]["model_calls"] == 1
    assert logger.data["cost"]["completion_tokens"] == 20
    transport.post_json.assert_called_once()
    assert "private_usage" not in str(logger.data)
    assert "private_name" not in str(logger.data)


def test_safe_error_is_backward_compatible_and_keeps_invalid_alias_unknown():
    from praxile.usage_evidence import token_value
    error = InvalidStructuredResponse("private code", {"usage": {
        "prompt_tokens": "private text", "input_tokens": 100,
        "completion_tokens": True, "unknown": "secret"}})
    assert isinstance(error, ModelError)
    assert error.code == "submission_envelope_invalid"
    assert error.reported_usage == {"prompt_tokens": None, "input_tokens": 100, "completion_tokens": None}
    assert token_value(error.reported_usage, "prompt_tokens", "input_tokens") is None
    assert "private" not in str(error.__dict__)


def test_no_usage_is_not_complete_zero():
    error = InvalidStructuredResponse("submission_envelope_invalid", {"choices": []})
    logger = TrajectoryLogger("Missing usage", {})
    logger.add_model_cost(error.reported_usage, status="failure")
    evidence = logger.data["cost"]["usage_evidence"]
    assert evidence["complete_tokens"] == {"prompt_tokens": None, "completion_tokens": None}
    assert evidence["calls"] == 1
