from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Protocol


class HTTPTransportError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class HTTPTransportUnavailable(HTTPTransportError):
    pass


class HTTPResponseDecodeError(HTTPTransportError):
    pass


class HTTPTransportCancelled(HTTPTransportUnavailable):
    pass


class HTTPTransport(Protocol):
    name: str

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        ...

    def stream_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        ...


class SimpleHTTPTransport:
    name = "urllib"

    def __init__(self, *, timeout_seconds: int = 30, max_retries: int = 0, retry_backoff_seconds: float = 0.25):
        self.timeout_seconds = max(1, int(timeout_seconds or 30))
        self.max_retries = max(0, int(max_retries or 0))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds or 0.0))

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
        effective_timeout = max(1, int(timeout or self.timeout_seconds))
        last_error: HTTPTransportError | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_requested)
            try:
                with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                    return _decode_json_response(response.read())
            except urllib.error.HTTPError as exc:
                detail = _safe_error_body(exc)
                retryable = exc.code >= 500
                last_error = HTTPTransportError(
                    f"HTTP {exc.code}: {detail or exc.reason}",
                    status_code=exc.code,
                    retryable=retryable,
                )
            except urllib.error.URLError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except TimeoutError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except json.JSONDecodeError as exc:
                raise HTTPResponseDecodeError(f"Invalid JSON response: {exc}") from exc
            if not last_error.retryable or attempt >= self.max_retries:
                break
            _sleep_with_cancel(self.retry_backoff_seconds * (attempt + 1), cancel_requested)
        assert last_error is not None
        raise last_error

    def stream_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "text/event-stream")
        body = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
        effective_timeout = max(1, int(timeout or self.timeout_seconds))
        last_error: HTTPTransportError | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_requested)
            try:
                with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                    yield from _json_events_from_sse_lines(response, cancel_requested=cancel_requested)
                return
            except urllib.error.HTTPError as exc:
                detail = _safe_error_body(exc)
                retryable = exc.code >= 500
                last_error = HTTPTransportError(
                    f"HTTP {exc.code}: {detail or exc.reason}",
                    status_code=exc.code,
                    retryable=retryable,
                )
            except urllib.error.URLError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except TimeoutError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            if not last_error.retryable or attempt >= self.max_retries:
                break
            _sleep_with_cancel(self.retry_backoff_seconds * (attempt + 1), cancel_requested)
        assert last_error is not None
        raise last_error


class HttpxTransport:
    name = "httpx"

    def __init__(self, *, timeout_seconds: int = 30, max_retries: int = 0, retry_backoff_seconds: float = 0.25):
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - depends on optional dependency
            raise RuntimeError("httpx is not installed. Install praxile[http] or use transport=simple.") from exc
        self.httpx = httpx
        self.timeout_seconds = max(1, int(timeout_seconds or 30))
        self.max_retries = max(0, int(max_retries or 0))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds or 0.0))
        self._client: Any | None = None

    def __enter__(self) -> "HttpxTransport":
        self._get_client()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = self.httpx.Client(timeout=self.timeout_seconds)
        return self._client

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        effective_timeout = max(1, int(timeout or self.timeout_seconds))
        last_error: HTTPTransportError | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_requested)
            try:
                response = self._get_client().post(
                    url,
                    headers=headers or {},
                    json=payload or {},
                    timeout=effective_timeout,
                )
                response.raise_for_status()
                return response.json()
            except self.httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code >= 500
                last_error = HTTPTransportError(
                    f"HTTP {exc.response.status_code}: {exc.response.text[:1000]}",
                    status_code=exc.response.status_code,
                    retryable=retryable,
                )
            except self.httpx.TimeoutException as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except self.httpx.HTTPError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except ValueError as exc:
                raise HTTPResponseDecodeError(f"Invalid JSON response: {exc}") from exc
            if not last_error.retryable or attempt >= self.max_retries:
                break
            _sleep_with_cancel(self.retry_backoff_seconds * (attempt + 1), cancel_requested)
        assert last_error is not None
        raise last_error

    def stream_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        request_headers = dict(headers or {})
        request_headers.setdefault("Accept", "text/event-stream")
        effective_timeout = max(1, int(timeout or self.timeout_seconds))
        last_error: HTTPTransportError | None = None
        for attempt in range(self.max_retries + 1):
            _raise_if_cancelled(cancel_requested)
            try:
                with self._get_client().stream(
                    "POST",
                    url,
                    headers=request_headers,
                    json=payload or {},
                    timeout=effective_timeout,
                ) as response:
                    response.raise_for_status()
                    yield from _json_events_from_sse_lines(response.iter_lines(), cancel_requested=cancel_requested)
                return
            except self.httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code >= 500
                last_error = HTTPTransportError(
                    f"HTTP {exc.response.status_code}: {exc.response.text[:1000]}",
                    status_code=exc.response.status_code,
                    retryable=retryable,
                )
            except self.httpx.TimeoutException as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            except self.httpx.HTTPError as exc:
                last_error = HTTPTransportUnavailable(str(exc), retryable=True)
            if not last_error.retryable or attempt >= self.max_retries:
                break
            _sleep_with_cancel(self.retry_backoff_seconds * (attempt + 1), cancel_requested)
        assert last_error is not None
        raise last_error


def make_transport(config: dict[str, Any] | None = None) -> HTTPTransport:
    config = config or {}
    mode = str(config.get("transport", "simple")).lower()
    timeout = int(config.get("timeout_seconds", 30) or 30)
    retries = int(config.get("max_retries", 0) or 0)
    backoff = float(config.get("retry_backoff_seconds", 0.25) or 0.0)
    if mode in {"httpx", "auto"}:
        try:
            return HttpxTransport(timeout_seconds=timeout, max_retries=retries, retry_backoff_seconds=backoff)
        except RuntimeError:
            if mode == "httpx":
                raise
    return SimpleHTTPTransport(timeout_seconds=timeout, max_retries=retries, retry_backoff_seconds=backoff)


def _decode_json_response(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise HTTPResponseDecodeError("JSON response must be an object")
    return payload


def _json_events_from_sse_lines(lines: Any, *, cancel_requested: Callable[[], bool] | None = None) -> Iterator[dict[str, Any]]:
    for data in _sse_data_events(lines, cancel_requested=cancel_requested):
        _raise_if_cancelled(cancel_requested)
        if data.strip() == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise HTTPResponseDecodeError(f"Invalid SSE JSON event: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPResponseDecodeError("SSE JSON event must be an object")
        yield payload


def _sse_data_events(lines: Any, *, cancel_requested: Callable[[], bool] | None = None) -> Iterator[str]:
    buffer: list[str] = []
    for raw_line in lines:
        _raise_if_cancelled(cancel_requested)
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if buffer:
                yield "\n".join(buffer)
                buffer = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    if buffer:
        yield "\n".join(buffer)


def _raise_if_cancelled(cancel_requested: Callable[[], bool] | None) -> None:
    if cancel_requested and cancel_requested():
        raise HTTPTransportCancelled("HTTP request cancelled")


def _sleep_with_cancel(seconds: float, cancel_requested: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _raise_if_cancelled(cancel_requested)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _safe_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:1000]
    except Exception:
        return ""
