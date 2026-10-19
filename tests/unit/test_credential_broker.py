import json
import os

import pytest

from praxile.eval.credential_broker import BrokerDenied, ChatCredentialBroker, read_private_key


@pytest.fixture
def broker():
    with ChatCredentialBroker(key="fixture-provider-secret", endpoint="https://provider.invalid/v1/chat/completions",
                              model="fixed", max_calls=2, output_budget=20, max_output=10) as value:
        value._forward = lambda body: {"choices": [{"message": {"content": "OK"}}]}
        yield value


def request(broker, **changes):
    return broker.chat("Bearer " + broker.capability, {"model": "fixed",
        "messages": [{"role": "user", "content": "hello"}], **changes})


def test_scoped_calls_and_output_reservations(broker):
    assert request(broker)["choices"]
    assert request(broker)["choices"]
    with pytest.raises(BrokerDenied):
        request(broker)
    assert broker.status()["remaining_output"] == 0
    assert "secret" not in json.dumps(broker.status())


@pytest.mark.parametrize("changes", [{"model": "other"}, {"stream": True}, {"tools": []},
    {"max_tokens": True}, {"max_tokens": 11}, {"temperature": float("nan")},
    {"messages": [{"role": "user", "content": [{"image_url": "https://private"}]}]},
    {"messages": []}, {"base_url": "https://attacker.invalid"}])
def test_rejected_requests_do_not_spend(broker, changes):
    with pytest.raises(BrokerDenied):
        request(broker, **changes)
    assert broker.status()["remaining_calls"] == 2


def test_failure_consumes_reservation_and_secret_echo_is_rejected(broker):
    broker._forward = lambda body: {"choices": [], "secret": "fixture-provider-secret"}
    with pytest.raises(RuntimeError, match="response rejected"):
        request(broker)
    assert broker.status()["remaining_calls"] == 1


def test_revoked_expired_and_incorrect_capabilities(broker):
    with pytest.raises(BrokerDenied):
        broker.chat("Bearer incorrect", {})
    broker._deadline = 0
    with pytest.raises(BrokerDenied):
        request(broker)
    broker.revoke()
    assert broker.status()["revoked"]


def test_single_flight_rejects_without_spending(broker):
    with broker._flight:
        with pytest.raises(BrokerDenied):
            request(broker)
    assert broker.status()["remaining_calls"] == 2


def test_broker_refuses_public_listener(broker):
    with pytest.raises(ValueError, match="loopback"):
        broker.start("0.0.0.0")


def test_fixed_upstream_does_not_follow_redirects_or_use_environment_proxy(broker, monkeypatch):
    import praxile.eval.credential_broker as module
    class Response:
        sent = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read1(self, limit):
            if self.sent:
                return b""
            self.sent = True
            return b'{"choices": []}'
    class Opener:
        def open(self, req, timeout):
            assert req.full_url == "https://provider.invalid/v1/chat/completions"
            assert req.get_header("Authorization") == "Bearer fixture-provider-secret"
            assert broker.capability not in str(req.headers)
            return Response()
    def opener(proxy, redirect):
        assert proxy.proxies == {}
        assert redirect.redirect_request(None, None, None, None, None, None) is None
        return Opener()
    monkeypatch.setattr(module, "build_opener", opener)
    assert ChatCredentialBroker._forward(broker, b"{}") == {"choices": []}


@pytest.mark.skipif(os.name != "posix", reason="POSIX host credential permissions")
def test_private_file_permissions_and_symlinks(tmp_path):
    path = tmp_path / "key"
    path.write_text("fixture-key\n")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_private_key(path)
    path.chmod(0o600)
    assert read_private_key(path) == "fixture-key"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        read_private_key(link)
