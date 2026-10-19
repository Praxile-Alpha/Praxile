from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.model import ModelRouter, ModelError
from praxile.structured_response import validate_response_contract, submission_content


CONTRACT = {"name": "submit_design", "schema": {"type": "object", "properties": {
    "decision": {"type": "string"}}, "required": ["decision"], "additionalProperties": False}}


def message(arguments='{"decision":"abstain"}', name="submit_design"):
    return {"content": None, "tool_calls": [{"type": "function",
        "function": {"name": name, "arguments": arguments}}]}


def test_router_sends_contract_and_never_executes_submission(tmp_path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {"fixture": {"type": "openai_compatible", "base_url": "http://localhost",
        "models": [{"name": "fixture", "supports_tools": True}]}}
    config.data["model_roles"]["proposal_composer"] = {"provider": "fixture", "model": "fixture"}
    router = ModelRouter(config)
    transport = SimpleNamespace(post_json=Mock(return_value={"choices": [
        {"message": message(), "finish_reason": "tool_calls"}], "usage": {"completion_tokens": 10}}))
    router.providers["fixture"].transport = transport
    response = router.chat([{"role": "user", "content": "test"}], purpose="proposal_composer",
                           response_contract=CONTRACT, max_tokens=100)
    body = transport.post_json.call_args.kwargs["payload"]
    assert body["tools"][0]["function"]["parameters"] == CONTRACT["schema"]
    assert body["tool_choice"]["function"]["name"] == CONTRACT["name"]
    assert body["max_tokens"] == 100 and response["content"] == '{"decision":"abstain"}'
    assert response["usage"]["completion_tokens"] == 10
    assert transport.post_json.call_count == 1


@pytest.mark.parametrize("arguments", ['[]', '{"x":NaN}', '{"x":1,"x":2}', '{broken', '"PRIVATE"'])
def test_bad_arguments_fail_without_source_text(arguments):
    with pytest.raises(ValueError) as caught:
        submission_content(message(arguments), CONTRACT)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("response", [{"content": '{"decision":"abstain"}'},
    message(name="PRIVATE_FUNCTION"), {"tool_calls": [*message()["tool_calls"], *message()["tool_calls"]]}])
def test_text_wrong_function_or_multiple_submissions_rejected(response):
    with pytest.raises(ValueError) as caught:
        submission_content(response, CONTRACT)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("contract", [{**CONTRACT, "name": "bad/name"},
    {**CONTRACT, "schema": {"type": "object", "$ref": "https://example.invalid/schema"}},
    {**CONTRACT, "schema": {"type": "array"}}, {**CONTRACT, "messages": []}])
def test_contract_rejects_unsafe_or_unsupported_shape(contract):
    with pytest.raises(ValueError):
        validate_response_contract(contract)


def test_provider_without_declared_tools_never_sends_contract():
    from praxile.model import OpenAICompatibleProvider
    transport = SimpleNamespace(post_json=Mock())
    provider = OpenAICompatibleProvider("fixture", {"base_url": "http://localhost", "models": ["fixture"]}, transport)
    with pytest.raises(ModelError, match="declared"):
        provider.chat({"model": "fixture", "messages": [], "response_contract": CONTRACT})
    transport.post_json.assert_not_called()


def test_structured_contract_is_not_silently_transferred_to_fallback(tmp_path):
    from praxile.model import ModelUnavailable
    config = Config.load(tmp_path)
    config.data["model_providers"] = {name: {"type": "openai_compatible", "base_url": "http://localhost",
        "models": [{"name": "fixture", "supports_tools": True}]} for name in ("primary", "secondary")}
    config.data["model_roles"]["proposal_composer"] = {"provider": "primary", "model": "fixture",
        "fallback": [{"provider": "secondary", "model": "fixture"}]}
    router = ModelRouter(config)
    primary = SimpleNamespace(post_json=Mock(side_effect=ModelUnavailable("unavailable")))
    secondary = SimpleNamespace(post_json=Mock())
    router.providers["primary"].transport = primary
    router.providers["secondary"].transport = secondary
    with pytest.raises(ModelError, match="fallback"):
        router.chat([], purpose="proposal_composer", response_contract=CONTRACT)
    assert primary.post_json.call_count == 1
    secondary.post_json.assert_not_called()
