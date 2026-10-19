from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.http_transport import SimpleHTTPTransport, HttpxTransport, HTTPTransportError, HTTPTransportCancelled


@pytest.fixture(params=["urllib", "httpx"])
def stream(request, monkeypatch):
    closed = []
    if request.param == "httpx":
        httpx = pytest.importorskip("httpx")
        transport = HttpxTransport(max_retries=2, retry_backoff_seconds=0)
        failure = httpx.ReadTimeout("secret body")
    else:
        transport = SimpleHTTPTransport(max_retries=2, retry_backoff_seconds=0)
        failure = TimeoutError("secret body")
    sequence = []
    @contextmanager
    def response(*args, **kwargs):
        item = sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        def lines():
            for value in item:
                if isinstance(value, Exception):
                    raise value
                yield value
        try:
            yield SimpleNamespace(raise_for_status=lambda: None, iter_lines=lines) if request.param == "httpx" else lines()
        finally:
            closed.append(True)
    opener = Mock(side_effect=response)
    if request.param == "urllib":
        monkeypatch.setattr("urllib.request.urlopen", opener)
    else:
        transport._client = SimpleNamespace(stream=opener, close=lambda: None)
    yield transport, sequence, failure, opener, closed
    if request.param == "httpx":
        transport.close()


def test_midstream_failure_never_replays_delivered_events(stream):
    transport, sequence, failure, opener, closed = stream
    sequence.append(['data: {"delta":"first"}\n', '\n', failure])
    events = transport.stream_json("https://example.invalid")
    assert next(events) == {"delta":"first"}
    with pytest.raises(HTTPTransportError) as caught:
        next(events)
    evidence = caught.value.transport_evidence
    assert evidence["events_delivered"] == 1 and evidence["automatic_replay_forbidden"]
    assert evidence["attempt_count"] == 1 and "secret body" not in str(evidence)
    assert opener.call_count == 1 and closed == [True]


def test_pre_event_failure_can_retry(stream):
    transport, sequence, failure, opener, closed = stream
    sequence.extend([failure, ['data: {"delta":"ok"}\n', '\n', 'data: [DONE]\n', '\n']])
    assert list(transport.stream_json("https://example.invalid")) == [{"delta":"ok"}]
    assert opener.call_count == 2 and closed == [True]


def test_cancellation_after_event_closes_without_retry(stream):
    transport, sequence, failure, opener, closed = stream
    sequence.append(['data: {"delta":"first"}\n', '\n', 'data: {"delta":"second"}\n', '\n'])
    cancelled = [False]
    events = transport.stream_json("https://example.invalid", cancel_requested=lambda: cancelled[0])
    next(events)
    cancelled[0] = True
    with pytest.raises(HTTPTransportCancelled) as caught:
        next(events)
    assert caught.value.transport_evidence["events_delivered"] == 1
    assert closed == [True] and opener.call_count == 1


def test_consumer_close_releases_stream(stream):
    transport, sequence, failure, opener, closed = stream
    sequence.append(['data: {"delta":"first"}\n', '\n'])
    events = transport.stream_json("https://example.invalid")
    next(events)
    events.close()
    assert closed == [True] and opener.call_count == 1
