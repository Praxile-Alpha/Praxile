import io
import json
from unittest.mock import Mock
import urllib.error

import pytest

from praxile.http_transport import SimpleHTTPTransport, HttpxTransport, HTTPTransportError, HTTPResponseDecodeError


def test_urllib_retries_are_counted_without_payload_leak(monkeypatch):
    opener = Mock(side_effect=[TimeoutError("SECRET"), io.BytesIO(b'{"answer":"ok"}')])
    monkeypatch.setattr("urllib.request.urlopen", opener)
    result = SimpleHTTPTransport(max_retries=1, retry_backoff_seconds=0).post_json("https://secret.invalid", headers={"Authorization":"SECRET"})
    evidence = result.transport_evidence
    assert result == {"answer":"ok"} and json.dumps(result) == '{"answer": "ok"}'
    assert evidence["attempt_count"] == 2 and evidence["failed_attempt_usage_unknown"]
    assert [item["status"] for item in evidence["attempts"]] == ["timeout", "success"]
    assert "SECRET" not in json.dumps(evidence) and "secret.invalid" not in json.dumps(evidence)


def test_urllib_failure_carries_all_transport_attempts(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=TimeoutError("read timed out")))
    with pytest.raises(HTTPTransportError) as error:
        SimpleHTTPTransport(max_retries=2, retry_backoff_seconds=0).post_json("https://example.invalid")
    assert error.value.transport_evidence["attempt_count"] == 3


@pytest.mark.parametrize("body", [b"invalid", b"[]"])
def test_decode_failure_is_not_retried(monkeypatch, body):
    opener = Mock(return_value=io.BytesIO(body))
    monkeypatch.setattr("urllib.request.urlopen", opener)
    with pytest.raises(HTTPResponseDecodeError) as error:
        SimpleHTTPTransport(max_retries=2).post_json("https://example.invalid")
    assert opener.call_count == 1 and error.value.transport_evidence["attempts"][0]["status"] == "decode_error"


def test_httpx_evidence_is_request_local():
    httpx = pytest.importorskip("httpx")
    transport = HttpxTransport(max_retries=1, retry_backoff_seconds=0)
    request = httpx.Request("POST", "https://example.invalid")
    transport._client = Mock()
    transport._client.post.side_effect = [httpx.ReadTimeout("SECRET"),
        httpx.Response(200, json={"ok":True}, request=request),
        httpx.Response(200, json={"ok":True}, request=request)]
    try:
        first = transport.post_json(str(request.url))
        second = transport.post_json(str(request.url))
        assert first.transport_evidence["attempt_count"] == 2
        assert second.transport_evidence["attempt_count"] == 1
        assert "SECRET" not in json.dumps(first.transport_evidence)
    finally:
        transport.close()
