from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.model import ModelRouter
from praxile.model_options import validate_request_options


@pytest.mark.parametrize("value", [{"messages": []}, {"max_tokens": 9999}, {"thinking": {"type": []}},
    {"reasoning_effort": []}, {"reasoning_split": 1}, {"thinking": {"type": "disabled", "extra": True}}])
def test_request_options_reject_overrides_and_invalid_types(value):
    with pytest.raises(ValueError):
        validate_request_options(value)


def test_role_options_reach_body_without_changing_request_budget(tmp_path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {"fixture": {"type": "openai_compatible", "base_url": "http://localhost",
                                                 "models": ["fixture"]}}
    config.data["model_roles"]["proposal_composer"] = {"provider": "fixture", "model": "fixture",
                                                        "request_options": {"thinking": {"type": "disabled"}}}
    router = ModelRouter(config)
    transport = SimpleNamespace(post_json=Mock(return_value={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}))
    router.providers["fixture"].transport = transport
    router.chat([{"role": "user", "content": "test"}], purpose="proposal_composer", max_tokens=42)
    body = transport.post_json.call_args.kwargs["payload"]
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] == 42 and body["messages"][0]["content"] == "test"
