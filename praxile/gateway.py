from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .channels import ChannelSystem
from .console import ConsolePage, render_console
from .config import Config
from .runtime import AgentRuntime
from .store import ExperienceStore


class GatewayError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


LOCAL_GATEWAY_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_GATEWAY_MAX_THREADS = 16


def validate_gateway_auth_config(host: str, token: str | None) -> None:
    if host not in LOCAL_GATEWAY_HOSTS and not token:
        raise RuntimeError("Non-localhost gateway requires an auth token. Use --token or bind to 127.0.0.1.")


def gateway_request_authorized(headers: Mapping[str, str], token: str | None) -> bool:
    if not token:
        return True
    bearer = headers.get("Authorization", "")
    header_token = headers.get("X-Praxile-Token", "")
    return bearer == f"Bearer {token}" or header_token == token


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler: BaseHTTPRequestHandler, status: int, page: ConsolePage) -> None:
    body = page.html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class GatewayApp:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()

    def store(self) -> tuple[Config, ExperienceStore]:
        config = Config.load(self.project_root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        return config, store

    def dispatch(self, method: str, path: str, query: dict[str, list[str]] | None = None, payload: dict[str, Any] | None = None) -> Any:
        query = query or {}
        payload = payload or {}
        config, store = self.store()
        if method == "GET" and path in {"/", "/console"}:
            return render_console(config)
        if method == "GET" and path == "/health":
            return {"status": "ok", "agent": "praxile", "project": str(config.paths.root)}
        if method == "GET" and path == "/history":
            return store.list_history(limit=int((query.get("limit") or ["20"])[0]))
        if method == "GET" and path == "/memory":
            return [item for item in store.retrieve(" ".join(query.get("q", [])), kinds=["memory"], limit=20)]
        if method == "GET" and path == "/channels":
            return [item.to_dict() for item in ChannelSystem(config).list_bindings()]
        if method == "GET" and path == "/review":
            item_id = (query.get("id") or [None])[0]
            proposal = store.find_proposal(item_id) if item_id else None
            if proposal:
                return proposal
            trajectory = store.get_trajectory(item_id) if item_id else store.latest_trajectory()
            if not trajectory:
                raise GatewayError(404, "No trajectory or proposal found")
            return trajectory
        if method == "POST" and path == "/run":
            task = payload.get("task")
            if not isinstance(task, str) or not task.strip():
                raise GatewayError(400, "`task` is required")
            test_commands = payload.get("test_commands")
            if test_commands is not None and not isinstance(test_commands, list):
                raise GatewayError(400, "`test_commands` must be a list")
            trajectory = AgentRuntime(config).run(
                task,
                test_commands=[str(item) for item in test_commands] if test_commands else None,
                max_steps=int(payload.get("max_steps")) if payload.get("max_steps") is not None else None,
                dry_run=bool(payload.get("dry_run", False)),
            )
            return {
                "task_id": trajectory["task_id"],
                "status": trajectory["result"]["status"],
                "summary": trajectory["result"]["summary"],
                "reward": trajectory.get("reward_report", {}).get("overall"),
                "proposals": trajectory.get("experience_candidates", []),
            }
        if method == "POST" and path == "/accept":
            proposal_id = payload.get("proposal_id")
            if not isinstance(proposal_id, str) or not proposal_id:
                raise GatewayError(400, "`proposal_id` is required")
            proposal = store.find_proposal(proposal_id, status="pending")
            if not proposal:
                raise GatewayError(404, "No pending proposal found")
            accepted = store.apply_proposal(proposal)
            return {"proposal_id": accepted["proposal_id"], "status": accepted["status"], "title": accepted["title"]}
        if method == "POST" and path == "/channels/bind":
            platform = payload.get("platform")
            channel_id = payload.get("channel_id")
            if platform not in {"telegram", "discord"} or not isinstance(channel_id, str):
                raise GatewayError(400, "`platform` and string `channel_id` are required")
            binding = ChannelSystem(config).bind(
                platform,
                channel_id,
                guild_id=payload.get("guild_id"),
                thread_id=payload.get("thread_id"),
                name=payload.get("name"),
                kind=payload.get("kind", "home"),
                mode=payload.get("mode", "notify"),
                token_env=payload.get("token_env"),
                require_mention=bool(payload.get("require_mention", True)),
                allow_free_response=bool(payload.get("allow_free_response", False)),
                auto_thread=payload.get("auto_thread"),
                skill=payload.get("skill"),
                prompt=payload.get("prompt"),
                project_scope=payload.get("project_scope", "current"),
                make_default=bool(payload.get("default", False)),
            )
            return binding.to_dict()
        if method == "POST" and path == "/channels/unbind":
            binding_id = payload.get("binding_id")
            if not isinstance(binding_id, str) or not binding_id:
                raise GatewayError(400, "`binding_id` is required")
            return ChannelSystem(config).unbind(binding_id).to_dict()
        raise GatewayError(404, f"No route for {method} {path}")


def build_handler(project_root: Path, token: str | None = None):
    app = GatewayApp(project_root)

    class PraxileGatewayHandler(BaseHTTPRequestHandler):
        server_version = "PraxileGateway/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def _handle(self, method: str) -> None:
            try:
                self._check_auth()
                parsed = urlparse(self.path)
                payload = self._read_json() if method == "POST" else {}
                result = app.dispatch(method, parsed.path, parse_qs(parsed.query), payload)
                if isinstance(result, ConsolePage):
                    html_response(self, 200, result)
                    return
                json_response(self, 200, {"ok": True, "result": result})
            except GatewayError as exc:
                json_response(self, exc.status, {"ok": False, "error": exc.message})
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})

        def _check_auth(self) -> None:
            if gateway_request_authorized(self.headers, token):
                return
            raise GatewayError(401, "Unauthorized")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length == 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise GatewayError(400, f"Invalid JSON: {exc}") from exc
            if not isinstance(data, dict):
                raise GatewayError(400, "JSON body must be an object")
            return data

    return PraxileGatewayHandler


class PooledHTTPServer(HTTPServer):
    """HTTPServer with a bounded worker pool instead of unbounded request threads."""

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], *, max_workers: int):
        self.max_workers = max(1, int(max_workers or DEFAULT_GATEWAY_MAX_THREADS))
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="praxile-gateway")
        super().__init__(server_address, handler_class)

    def process_request(self, request: Any, client_address: Any) -> None:
        self._executor.submit(self._process_request_worker, request, client_address)

    def _process_request_worker(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)


def serve_gateway(project_root: Path, *, host: str = "127.0.0.1", port: int = 8765, token: str | None = None) -> PooledHTTPServer:
    validate_gateway_auth_config(host, token)
    config = Config.load(project_root.resolve())
    max_threads = int(config.get("gateway", "max_threads", default=DEFAULT_GATEWAY_MAX_THREADS) or DEFAULT_GATEWAY_MAX_THREADS)
    handler = build_handler(project_root.resolve(), token=token)
    server = PooledHTTPServer((host, port), handler, max_workers=max_threads)
    return server
