import json
import os
import subprocess
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from praxile.eval.credential_broker import ChatCredentialBroker

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.timeout(20)]


def test_structured_provider_uses_shared_judge_quota_over_http(monkeypatch):
    from praxile.eval.credential_broker import SharedChatBudget
    from praxile.model import OpenAICompatibleProvider, ModelUnavailable
    shared = SharedChatBudget(max_calls=2, output_budget=20, ttl_seconds=30)
    shared.reserve("executor", 10)
    contract = {"name": "submit_review", "schema": {"type": "object"}}
    with ChatCredentialBroker(key="host-fixture-secret", endpoint="https://provider.invalid/v1/chat/completions",
            model="fixed", max_calls=3, output_budget=30, max_output=10, shared_budget=shared,
            budget_role="judge", response_contracts=[contract]) as broker:
        forwarded = []
        def forward(raw):
            forwarded.append(json.loads(raw))
            return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
                "name": "submit_review", "arguments": '{"items": []}'}}]}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
        broker._forward = forward
        host, port = broker.start()
        thread = broker._thread
        monkeypatch.setenv("PRAXILE_TEST_JUDGE_CAPABILITY", broker.capability)
        provider = OpenAICompatibleProvider("judge", {"base_url": f"http://{host}:{port}/v1",
            "api_key_env": "PRAXILE_TEST_JUDGE_CAPABILITY", "max_retries": 0,
            "models": [{"name": "fixed", "supports_tools": True}]})
        request = {"model": "fixed", "messages": [{"role": "user", "content": "public fixture"}],
                   "max_tokens": 10, "timeout": 3, "response_contract": contract}
        try:
            result = provider.chat(request)
            assert json.loads(result["content"]) == {"items": []}
            with pytest.raises(ModelUnavailable):
                provider.chat(request)
        finally:
            close = getattr(provider.transport, "close", None)
            if close:
                close()
        assert len(forwarded) == 1
        assert shared.status()["roles"]["judge"]["reserved_calls"] == 1
        assert broker.status()["usage"]["total_tokens"] == {"input": 5, "output": 3}
    assert not thread.is_alive()


def test_real_http_scoped_auth_revocation_and_cleanup():
    with ChatCredentialBroker(key="fixture-key", endpoint="https://provider.invalid/v1/chat/completions",
                              model="fixed", max_calls=2, output_budget=20, max_output=10) as broker:
        forwarded = []
        def forward(body):
            forwarded.append(json.loads(body))
            return {"choices": [{"message": {"content": "OK"}}]}
        broker._forward = forward
        host, port = broker.start()
        thread = broker._thread
        payload = json.dumps({"model": "fixed", "messages": [{"role": "user", "content": "hello"}]}).encode()
        def call(token, route="/v1/chat/completions"):
            req = Request(f"http://{host}:{port}{route}", data=payload, headers={"Authorization": "Bearer " + token})
            return urlopen(req, timeout=3)
        with pytest.raises(HTTPError) as rejected:
            call("wrong")
        assert rejected.value.code == 403
        rejected.value.close()
        with call(broker.capability) as response:
            assert json.load(response)["choices"]
        assert len(forwarded) == 1
        broker.revoke()
        with pytest.raises(HTTPError) as revoked:
            call(broker.capability)
        assert revoked.value.code == 403
        revoked.value.close()
        assert len(forwarded) == 1
    assert not thread.is_alive()


@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_DOCKER_BROKER"), reason="explicit Docker Desktop opt-in")
@pytest.mark.timeout(60)
def test_docker_desktop_reaches_host_loopback_without_provider_key():
    with ChatCredentialBroker(key="host-only-fixture-key", endpoint="https://provider.invalid/v1/chat/completions",
                              model="fixed", max_calls=1, output_budget=10, max_output=10) as broker:
        broker._forward = lambda body: {"choices": [{"message": {"content": "OK"}}]}
        _, port = broker.start()
        script = '''import json,sys,urllib.request
token=sys.stdin.read().strip()
body=json.dumps({"model":"fixed","messages":[{"role":"user","content":"hello"}]}).encode()
request=urllib.request.Request(sys.argv[1],data=body,headers={"Authorization":"Bearer "+token})
with urllib.request.urlopen(request,timeout=10) as response:
    assert json.load(response)["choices"][0]["message"]["content"]=="OK"
print("broker reachable")
'''
        name = "praxile-broker-test-" + uuid4().hex
        try:
            result = subprocess.run(["docker", "run", "--rm", "--name", name, "-i", "python:3.12-slim", "python", "-c", script,
                f"http://host.docker.internal:{port}/v1/chat/completions"],
                input=broker.capability, text=True, capture_output=True, timeout=40)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        assert result.returncode == 0, "Docker broker connection failed (output intentionally withheld)"
        assert result.stdout.strip() == "broker reachable"
        assert broker.status()["remaining_calls"] == 0
