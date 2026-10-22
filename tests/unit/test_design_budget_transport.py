import io
import json
import urllib.error

import pytest

from praxile.eval.design_budget_transport import DesignBudgetTransport
from praxile.eval.persistent_chat_budget import PersistentChatBudget
from praxile.eval.credential_broker import BrokerDenied
from praxile.http_transport import HTTPTransportUnavailable, HTTPTransportCancelled


def test_exact_request_bytes_shared_admission_and_no_retry(tmp_path, monkeypatch):
    quota = PersistentChatBudget.create(tmp_path / "quota.sqlite", max_calls=2,
        output_budget=40, ttl_seconds=600, request_byte_budget=10000)
    requests = []
    def forward(request, **kwargs):
        requests.append(request.data)
        if len(requests) == 1:
            raise urllib.error.URLError("fixture failure")
        return io.BytesIO(b'{"choices": []}')
    monkeypatch.setattr("urllib.request.urlopen", forward)
    transport = DesignBudgetTransport(quota)
    payload = {"model": "fixture", "messages": [{"role": "user", "content": "你好"}], "max_tokens": 20}
    with pytest.raises(HTTPTransportUnavailable):
        transport.post_json("https://fixture.invalid", payload=payload)
    assert len(requests) == 1
    reopened = DesignBudgetTransport(PersistentChatBudget(tmp_path / "quota.sqlite"))
    assert reopened.post_json("https://fixture.invalid", payload=payload) == {"choices": []}
    with pytest.raises(BrokerDenied):
        reopened.post_json("https://fixture.invalid", payload=payload)
    assert len(requests) == 2
    assert quota.status()["remaining_request_bytes"] == 10000 - sum(map(len, requests))
    assert quota.status()["roles"]["proposal_composer"]["reserved_calls"] == 2


def test_cancel_or_unbounded_request_never_dispatches(tmp_path, monkeypatch):
    quota = PersistentChatBudget.create(tmp_path / "quota.sqlite", max_calls=1, output_budget=40, ttl_seconds=600)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("network accessed"))
    transport = DesignBudgetTransport(quota)
    with pytest.raises(HTTPTransportCancelled):
        transport.post_json("https://fixture.invalid", payload={"max_tokens": 20}, cancel_requested=lambda: True)
    for payload in ({}, {"max_tokens": True}, {"max_tokens": 0}):
        with pytest.raises(ValueError):
            transport.post_json("https://fixture.invalid", payload=payload)
    with pytest.raises(ValueError):
        transport.stream_json("https://fixture.invalid")
    assert quota.status()["remaining_calls"] == 1


@pytest.mark.parametrize("kind", ["openai", "anthropic"])
def test_configured_router_installs_budget_transport(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    from praxile.config import Config
    from praxile.model import OpenAICompatibleProvider, AnthropicProvider
    from praxile.eval.v2 import configured_composer
    provider_type = OpenAICompatibleProvider if kind == "openai" else AnthropicProvider
    provider = provider_type("fixture", {"base_url": "https://fixture.invalid/v1", "api_key_env": "FIXTURE_KEY"})
    monkeypatch.setenv("FIXTURE_KEY", "fixture")
    router = SimpleNamespace(providers={"fixture": provider}, route=lambda *args, **kwargs: None,
        describe_route=lambda *args, **kwargs: {"provider": "fixture", "model": "fixture"})
    monkeypatch.setattr(configured_composer, "ModelRouter", lambda config: router)
    def forward(request, **kwargs):
        assert json.loads(request.data)["max_tokens"] == 20
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}],
            "content": [{"type": "text", "text": "ok"}], "usage": {}}).encode())
    monkeypatch.setattr("urllib.request.urlopen", forward)
    quota = PersistentChatBudget.create(tmp_path / "quota.sqlite", max_calls=1, output_budget=40, ttl_seconds=600)
    scope = {"repositories": ["repo"], "task_signals": ["repair"], "does_not_apply_when": ["unrelated"]}
    with configured_composer.configured_design_router(Config.load(tmp_path), cycle_id="c", applies_to=scope,
                                                      shared_chat_budget=quota) as (_, identity):
        assert identity["request_admission"] == "shared_quota_nonretrying_transport_v1"
        assert provider.chat({"model": "fixture", "messages": [{"role": "user", "content": "test"}],
                              "max_tokens": 20})["content"] == "ok"
    assert quota.status()["remaining_calls"] == 0
