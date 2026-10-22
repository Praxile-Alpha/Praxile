"""Bounded, host-owned chat capability; never an arbitrary forwarding proxy."""
from __future__ import annotations

import hmac
import json
import math
import secrets
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class BrokerDenied(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_private_key(path: Path) -> str:
    # O_NOFOLLOW closes the final-component symlink race on supported hosts.
    import os
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("private credential loading requires a POSIX host")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 8192:
            raise ValueError("credential must be a private regular file of at most 8192 bytes")
        key = handle.read(8193).strip()
    if not key or len(key) > 4096 or any(c.isspace() or ord(c) < 33 for c in key):
        raise ValueError("credential file must contain one raw key")
    return key


class SharedChatBudget:
    """Controller-owned, process-local quota shared across broker instances.

    Reservations are never refunded, including on timeout or missing usage.
    This is not a total-token, monetary, or whole-task wall-time guarantee.
    """

    def __init__(self, *, max_calls, output_budget, ttl_seconds, request_byte_budget=None):
        if any(type(value) is not int or not 1 <= value <= limit for value, limit in (
                (max_calls, 10000), (output_budget, 100_000_000), (ttl_seconds, 86400))):
            raise ValueError("invalid shared chat budget")
        if request_byte_budget is not None and (type(request_byte_budget) is not int
                or not 1 <= request_byte_budget <= 10**9):
            raise ValueError("invalid request byte budget")
        self._lock = threading.Lock()
        self._deadline = time.monotonic() + ttl_seconds
        self._calls, self._output = max_calls, output_budget
        self._roles = {}
        self._request_bytes = request_byte_budget

    def reserve(self, role, maximum, *, request_bytes=None):
        if (not isinstance(role, str) or not role or len(role) > 80
                or type(maximum) is not int or maximum <= 0):
            raise ValueError("invalid shared reservation")
        if request_bytes is not None and (type(request_bytes) is not int or request_bytes < 0):
            raise ValueError("invalid request byte reservation")
        with self._lock:
            if time.monotonic() >= self._deadline:
                raise BrokerDenied("shared budget expired")
            if self._calls < 1 or self._output < maximum:
                raise BrokerDenied("shared budget exhausted")
            if self._request_bytes is not None:
                if request_bytes is None or request_bytes <= 0 or request_bytes > self._request_bytes:
                    raise BrokerDenied("shared request byte budget exhausted or measurement missing")
                self._request_bytes -= request_bytes
            self._calls -= 1
            self._output -= maximum
            usage = self._roles.setdefault(role, {"reserved_calls": 0, "reserved_output": 0})
            usage["reserved_calls"] += 1
            usage["reserved_output"] += maximum
            if self._request_bytes is not None:
                usage["reserved_request_bytes"] = usage.get("reserved_request_bytes", 0) + request_bytes

    def status(self):
        with self._lock:
            return {"schema_version": "praxile.shared_chat_budget.v1",
                    "remaining_calls": self._calls, "remaining_output": self._output,
                    "remaining_request_bytes": self._request_bytes,
                    "expired": time.monotonic() >= self._deadline,
                    "roles": {role: dict(value) for role, value in self._roles.items()},
                    "scope": ("process_local_request_admission_output_and_request_bytes" if self._request_bytes is not None
                              else "process_local_request_admission_and_output_reservations"),
                    "total_token_bound_verified": False, "cost_bound_verified": False}


class ChatCredentialBroker:
    """Single-flight, expiring chat endpoint. Issued capabilities are still secrets.

    Reservation accounting bounds requested output, not dollars or input tokens.
    Revocation prevents new calls; it cannot undo an admitted provider request.
    """

    def __init__(self, *, key: str, endpoint: str, model: str, max_calls: int,
                 output_budget: int, max_output: int = 4096, ttl_seconds: int = 900,
                 request_timeout: int = 60, request_options: dict | None = None,
                 shared_budget: SharedChatBudget | None = None, budget_role: str = "executor",
                 response_contracts: list[dict] | None = None):
        from ..structured_response import submission_request
        self._submissions = set()
        if response_contracts is not None:
            if not isinstance(response_contracts, list) or not 1 <= len(response_contracts) <= 32:
                raise ValueError("broker requires 1..32 frozen response contracts")
            for contract in response_contracts:
                encoded = json.dumps(submission_request(contract), sort_keys=True, allow_nan=False)
                if encoded in self._submissions:
                    raise ValueError("duplicate broker response contract")
                self._submissions.add(encoded)
            if sum(len(value.encode()) for value in self._submissions) > 1024 * 1024:
                raise ValueError("broker response contracts exceed 1 MiB")
        if shared_budget is not None and not isinstance(shared_budget, SharedChatBudget):
            raise ValueError("shared budget must be a SharedChatBudget")
        if not isinstance(budget_role, str) or not budget_role or len(budget_role) > 80:
            raise ValueError("invalid budget role")
        self._shared_budget, self._budget_role = shared_budget, budget_role
        from ..model_options import validate_request_options
        self._request_options = validate_request_options({} if request_options is None else request_options)
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or not url.path.endswith("/chat/completions")):
            raise ValueError("broker requires a fixed HTTPS chat endpoint")
        limits = [(max_calls, 1, 1000), (output_budget, 1, 10_000_000),
                  (max_output, 1, 262144), (ttl_seconds, 1, 3600), (request_timeout, 1, 120)]
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in limits):
            raise ValueError("invalid broker budget")
        if not key or any(c.isspace() for c in key) or not model.strip():
            raise ValueError("key and model are required")
        self._key, self.endpoint, self.model = key, endpoint, model
        self._capability = secrets.token_urlsafe(32)
        self._deadline = time.monotonic() + ttl_seconds
        self._remaining_calls, self._remaining_output = max_calls, output_budget
        self.max_output, self.request_timeout = max_output, request_timeout
        self._revoked = False
        self._lock = threading.Lock()
        self._flight = threading.Lock()
        self._server = self._thread = None
        self._admitted = self._responses = self._failed = 0
        self._latency_seconds = 0.0
        self._tokens = {"input": 0, "output": 0}
        self._usage_reports = {"input": 0, "output": 0}

    @property
    def capability(self) -> str:
        return self._capability

    def revoke(self) -> None:
        with self._lock:
            self._revoked = True

    def status(self) -> dict:
        with self._lock:
            return {"revoked": self._revoked, "expired": time.monotonic() >= self._deadline,
                    "remaining_calls": self._remaining_calls, "remaining_output": self._remaining_output,
                    "shared_budget": self._shared_budget.status() if self._shared_budget is not None else None,
                    "usage": {
                        "schema_version": "praxile.broker_usage.v1",
                        "admitted_calls": self._admitted, "responses": self._responses,
                        "failed_calls": self._failed,
                        "inflight_calls": self._admitted - self._responses - self._failed,
                        "latency_seconds": round(self._latency_seconds, 6),
                        "known_tokens": dict(self._tokens),
                        "reported_calls": dict(self._usage_reports),
                        "total_tokens": {key: self._tokens[key] if self._usage_reports[key] == self._admitted else None
                                         for key in self._tokens},
                        "cost_usd": None, "source": "provider_response",
                    }}

    def _authorize(self, authorization: str) -> None:
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + self._capability).encode()):
            raise BrokerDenied("invalid capability")
        with self._lock:
            if self._revoked or time.monotonic() >= self._deadline:
                raise BrokerDenied("capability expired or revoked")

    def chat(self, authorization: str, payload: dict) -> dict:
        self._authorize(authorization)
        allowed = {"model", "messages", "max_tokens", "max_completion_tokens", "temperature", "stream"}
        if self._submissions:
            allowed.update({"tools", "tool_choice"})
        allowed.update(self._request_options)
        if not isinstance(payload, dict) or set(payload) - allowed or payload.get("model") != self.model:
            raise BrokerDenied("request outside model capability")
        if self._submissions:
            try:
                submission = json.dumps({key: payload.get(key) for key in ("tools", "tool_choice")},
                                        sort_keys=True, allow_nan=False)
            except (ValueError, TypeError, RecursionError) as error:
                raise BrokerDenied("invalid structured submission") from error
            if submission not in self._submissions:
                raise BrokerDenied("submission differs from frozen response contracts")
        for key, value in self._request_options.items():
            if key in payload and json.dumps(payload[key], sort_keys=True) != json.dumps(value, sort_keys=True):
                raise BrokerDenied("request options differ from fixed model capability")
        if payload.get("stream", False) is not False:
            raise BrokerDenied("streaming is not supported")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not 1 <= len(messages) <= 256:
            raise BrokerDenied("invalid messages")
        for message in messages:
            if (not isinstance(message, dict) or set(message) != {"role", "content"}
                    or not isinstance(message["role"], str)
                    or message["role"] not in {"system", "user", "assistant"}
                    or not isinstance(message["content"], str)):
                raise BrokerDenied("only text chat messages are permitted")
        if "max_tokens" in payload and "max_completion_tokens" in payload:
            raise BrokerDenied("ambiguous output limits")
        output_field = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
        maximum = payload.get(output_field, self.max_output)
        if type(maximum) is not int or not 1 <= maximum <= self.max_output:
            raise BrokerDenied("invalid output limit")
        temperature = payload.get("temperature", 1)
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise BrokerDenied("invalid temperature")
        body = json.dumps({**payload, **self._request_options, output_field: maximum}, allow_nan=False).encode()
        if len(body) > 1024 * 1024:
            raise BrokerDenied("request too large")
        if not self._flight.acquire(blocking=False):
            raise BrokerDenied("another request is active")
        started = None
        returned = False
        try:
            self._authorize(authorization)
            with self._lock:
                if self._revoked or time.monotonic() >= self._deadline:
                    raise BrokerDenied("capability expired or revoked")
                if self._remaining_calls < 1 or self._remaining_output < maximum:
                    raise BrokerDenied("capability budget exhausted")
                if self._shared_budget is not None:
                    self._shared_budget.reserve(self._budget_role, maximum, request_bytes=len(body))
                self._remaining_calls -= 1
                self._remaining_output -= maximum
                self._admitted += 1
                started = time.monotonic()
            # Failed requests consume reservations: no retry-driven quota bypass.
            result = self._forward(body)
            if self._key.encode() in json.dumps(result).encode():
                raise RuntimeError("provider response rejected")
            usage = result.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            with self._lock:
                for key, field in (("input", "prompt_tokens"), ("output", "completion_tokens")):
                    value = usage.get(field)
                    if type(value) is int and 0 <= value <= 10**12:
                        self._tokens[key] += value
                        self._usage_reports[key] += 1
                self._responses += 1
                returned = True
            return result
        finally:
            if started is not None:
                with self._lock:
                    self._latency_seconds += time.monotonic() - started
                    if not returned:
                        self._failed += 1
            self._flight.release()

    def _forward(self, body: bytes) -> dict:
        request = Request(self.endpoint, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._key})
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self.request_timeout) as response:
            deadline = time.monotonic() + self.request_timeout
            chunks, size = [], 0
            while size <= 8 * 1024 * 1024:
                if time.monotonic() >= deadline:
                    raise TimeoutError("provider response deadline")
                chunk = response.read1(min(65536, 8 * 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("provider response too large")
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            raise RuntimeError("invalid provider response")
        return result

    def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple[str, int]:
        if host != "127.0.0.1":
            raise ValueError("broker currently supports loopback IPv4 only")
        if self._server is not None:
            raise RuntimeError("broker already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass

            def do_POST(self):
                code, result = 403, {"error": "request denied"}
                try:
                    if self.path != "/v1/chat/completions" or self.headers.get("Transfer-Encoding"):
                        raise BrokerDenied("invalid route")
                    owner._authorize(self.headers.get("Authorization", ""))
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 1024 * 1024:
                        raise BrokerDenied("invalid body length")
                    size, chunks, deadline = int(lengths[0]), [], time.monotonic() + 5
                    while size:
                        if time.monotonic() >= deadline:
                            raise BrokerDenied("body deadline exceeded")
                        chunk = self.rfile.read1(min(size, 65536))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size -= len(chunk)
                    raw = b"".join(chunks)
                    if len(raw) != int(lengths[0]):
                        raise BrokerDenied("incomplete body")
                    payload = json.loads(raw)
                    result = owner.chat(self.headers.get("Authorization", ""), payload)
                    code = 200
                except (BrokerDenied, ValueError):
                    pass
                except Exception:
                    code, result = 502, {"error": "upstream request failed"}
                data = json.dumps(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

        self._server = HTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self._thread.start()
        return self._server.server_address

    def close(self) -> None:
        self.revoke()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
