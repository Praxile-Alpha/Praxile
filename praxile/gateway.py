from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .audit import build_project_audit_bundle, build_project_audit_check
from .channels import ChannelSystem, DEFAULT_TOKEN_ENVS, SUPPORTED_PLATFORMS
from .console import ConsolePage, render_console
from .config import Config, ProjectPaths
from .github import GitHubConnector, GitHubIntegrationError, build_pr_comment_body, import_actions_artifacts
from .model import ModelError, ModelRouter, ModelUnavailable
from .reflect import ReflectEngine, ReflectScope
from .runtime import AgentRuntime
from .security import SafetyPolicy
from .specs import build_spec_context, check_spec_file, verify_spec_compliance
from .store import ExperienceStore
from .tools import READ_ONLY_ACTIONS, ToolRegistry
from .utils import new_id, path_is_relative_to, read_json, shorten, utc_now, write_json


class GatewayError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class SSEStream:
    def __init__(self, job: "RunJob", *, after: int = 0, timeout_seconds: int = 60):
        self.job = job
        self.after = max(0, int(after or 0))
        self.timeout_seconds = max(1, int(timeout_seconds or 60))


class RunJob:
    def __init__(self, *, project_root: Path, payload: dict[str, Any], session_id: str | None = None):
        self.project_root = project_root
        self.payload = copy.deepcopy(payload)
        self.session_id = session_id
        self.job_id = new_id("job")
        self.status = "queued"
        self.created_at = utc_now()
        self.updated_at = self.created_at
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self._events: list[dict[str, Any]] = []
        self._seq = 0
        self._stop_requested = False
        self._stop_reason: str | None = None
        self._lock = threading.RLock()
        self.add_event("queued", "queued", "Run job queued.", {"session_id": session_id})

    def add_event(self, event_type: str, stage: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            self.updated_at = utc_now()
            event = {
                "seq": self._seq,
                "type": event_type,
                "stage": stage,
                "message": message,
                "data": data or {},
                "job_id": self.job_id,
                "created_at": self.updated_at,
            }
            self._events.append(event)
            self._events = self._events[-500:]
            return dict(event)

    def request_stop(self, reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._stop_requested = True
            self._stop_reason = reason or "manual stop request"
            self.status = "cancelling" if self.status in {"queued", "running"} else self.status
            event = self.add_event("stop_requested", "cancelling", self._stop_reason, {"status": self.status})
            return event

    def cancel_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def events_after(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if int(event.get("seq") or 0) > after]

    def snapshot(self, *, after: int = 0, include_events: bool = True) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "session_id": self.session_id,
                "status": self.status,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "stop_requested": self._stop_requested,
                "stop_reason": self._stop_reason,
                "result": self.result,
                "error": self.error,
                "events": self.events_after(after) if include_events else [],
                "latest_seq": self._seq,
            }

    def set_status(self, status: str, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.status = status
            self.add_event("stage", stage, message, data)

    def runtime_event(self, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        self.add_event("runtime_stage", stage, message, data or {})


class RunJobManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._jobs: dict[str, RunJob] = {}
        self._lock = threading.RLock()

    def start(self, payload: dict[str, Any], *, session_id: str | None = None) -> RunJob:
        job = RunJob(project_root=self.project_root, payload=payload, session_id=session_id)
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(target=self._run_job, args=(job,), name=f"praxile-job-{job.job_id}", daemon=True)
        job.thread = thread
        thread.start()
        return job

    def get(self, job_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.snapshot(include_events=False) for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)]

    def _run_job(self, job: RunJob) -> None:
        try:
            if job.cancel_requested():
                job.set_status("cancelled", "cancelled", "Run job was cancelled before start.")
                return
            job.set_status("running", "initializing", "Loading project state and runtime.")
            config = Config.load(self.project_root)
            store = ExperienceStore(config.paths)
            store.initialize(config)
            job.add_event("stage", "running", "Agent runtime started.")
            result = _run_task(
                config,
                store,
                job.payload,
                cancel_requested=job.cancel_requested,
                progress_callback=job.runtime_event,
            )
            job.result = result
            if job.session_id:
                _append_chat_job_result(config, job.session_id, job.job_id, result)
            status = "cancelled" if job.cancel_requested() and result.get("status") != "completed" else str(result.get("status") or "completed")
            job.set_status(status, status, str(result.get("summary") or status), {"task_id": result.get("task_id")})
        except Exception as exc:  # pragma: no cover - defensive async envelope
            job.error = f"{exc.__class__.__name__}: {exc}"
            job.set_status("failed", "failed", job.error)
            if job.session_id:
                try:
                    config = Config.load(self.project_root)
                    _append_chat_error(config, job.session_id, job.job_id, job.error)
                except Exception:
                    pass


LOCAL_GATEWAY_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_GATEWAY_MAX_THREADS = 16
MODEL_ROLE_CATALOG = [
    ("coding_agent", "Core execution", "required", "Primary coding and action loop"),
    ("evidence_extraction", "Experience extraction", "recommended", "Extract reusable evidence from runs"),
    ("experience_reflection", "Experience extraction", "recommended", "Summarize learning and propose durable experience"),
    ("proposal_composer", "Experience extraction", "recommended", "Compose reviewable proposal text"),
    ("review_recommendation", "Review and reward", "recommended", "Classify review recommendations"),
    ("reward_judge", "Review and reward", "optional", "Optional quality judging"),
    ("feedback_classifier", "Review and reward", "recommended", "Route natural-language feedback"),
    ("attribution_judge", "Semantic judges", "optional", "Judge whether loaded assets influenced a run"),
    ("counterexample_checker", "Semantic judges", "optional", "Check pattern counterexamples"),
    ("pattern_mining", "Semantic judges", "optional", "Mine recurring cross-run patterns"),
    ("project_pattern_composer", "Semantic judges", "optional", "Compose high-quality project pattern cards"),
    ("deep_project_pattern_mining", "Semantic judges", "optional", "Higher-cost pattern mining"),
    ("cheap_reasoner", "Utility", "optional", "Low-cost local reasoning fallback"),
    ("embedding", "Utility", "required", "Retrieval embedding/vector role"),
]


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


def sse_response(handler: BaseHTTPRequestHandler, stream: SSEStream) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    deadline = time.monotonic() + stream.timeout_seconds
    last_seq = stream.after
    terminal = {"completed", "failed", "cancelled", "needs_human"}
    while time.monotonic() < deadline:
        events = stream.job.events_after(last_seq)
        for event in events:
            last_seq = max(last_seq, int(event.get("seq") or 0))
            payload = json.dumps(event, ensure_ascii=False)
            handler.wfile.write(f"id: {last_seq}\n".encode("utf-8"))
            handler.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            handler.wfile.flush()
        snapshot = stream.job.snapshot(include_events=False)
        if snapshot["status"] in terminal and not events:
            payload = json.dumps({"seq": last_seq + 1, "type": "done", "stage": snapshot["status"], "job_id": stream.job.job_id}, ensure_ascii=False)
            handler.wfile.write(f"event: done\ndata: {payload}\n\n".encode("utf-8"))
            handler.wfile.flush()
            return
        time.sleep(0.25)


class GatewayApp:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.jobs = RunJobManager(self.project_root)

    def store(self) -> tuple[Config, ExperienceStore]:
        config = Config.load(self.project_root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        return config, store

    def check_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        config, _store = self.store()
        decision = SafetyPolicy(config).check_tool_call(tool_name, args or {}, context=context)
        return {
            "tool": tool_name,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "risk_level": decision.risk_level,
        }

    def dispatch(self, method: str, path: str, query: dict[str, list[str]] | None = None, payload: dict[str, Any] | None = None) -> Any:
        query = query or {}
        payload = payload or {}
        config, store = self.store()
        api_path = path[4:] if path == "/api" or path.startswith("/api/") else path
        parts = [unquote(part) for part in api_path.strip("/").split("/") if part]
        if method == "GET" and path in {"/", "/console"}:
            return render_console(config)
        if path == "/api":
            return {"routes": _api_routes()}
        if path.startswith("/api/"):
            return self._dispatch_api(method, api_path, parts, query, payload, config, store)
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

    def _dispatch_api(
        self,
        method: str,
        path: str,
        parts: list[str],
        query: dict[str, list[str]],
        payload: dict[str, Any],
        config: Config,
        store: ExperienceStore,
    ) -> Any:
        if method == "GET" and path == "/status":
            return _status_payload(config, store)
        if method == "GET" and path == "/doctor":
            return _doctor_payload(config, store)
        if method == "GET" and path == "/config":
            return _sanitized_config(config)

        if parts[:2] == ["chat", "sessions"]:
            return self._chat_api(method, parts, payload, config, store)

        if method == "GET" and parts == ["runs"]:
            return [_compact_history_row(row) for row in store.list_history(limit=_query_int(query, "limit", 50))]
        if len(parts) >= 2 and parts[:2] == ["runs", "jobs"]:
            return self._run_jobs_api(method, parts, query, payload)
        if method == "POST" and parts == ["runs"]:
            return _run_task(config, store, payload)
        if len(parts) >= 2 and parts[0] == "runs":
            return _run_api(method, parts, store)

        if method == "GET" and parts == ["models", "providers"]:
            return _model_providers(config)
        if method == "GET" and parts == ["models", "roles"]:
            return _model_roles(config)
        if method == "GET" and parts == ["models", "stats"]:
            return store.model_routing_stats(limit=_query_int(query, "limit", 200))
        if method == "POST" and parts == ["models", "test"]:
            return _test_model_role(config, payload)
        if method == "POST" and parts == ["models", "test-all"]:
            timeout = payload.get("timeout_seconds") if isinstance(payload.get("timeout_seconds"), int) else None
            return ModelRouter(config).check_routes(timeout_seconds=timeout)
        if method == "POST" and parts == ["models", "providers"]:
            return _upsert_model_provider(config, payload)
        if method in {"POST", "PATCH"} and len(parts) == 3 and parts[:2] == ["models", "providers"]:
            return _upsert_model_provider(config, {**payload, "provider_id": parts[2]})
        if method in {"POST", "PATCH"} and len(parts) == 3 and parts[:2] == ["models", "roles"]:
            return _update_model_role(config, parts[2], payload)

        if method == "GET" and parts == ["channels"]:
            return _channels_payload(config)
        if method == "POST" and parts == ["channels", "bind"]:
            return _bind_channel(config, payload)
        if method == "POST" and len(parts) == 3 and parts[0] == "channels" and parts[2] == "unbind":
            return _unbind_channel(config, parts[1], payload)

        if method == "GET" and parts == ["tools"]:
            return _tool_catalog(config)
        if method == "GET" and parts == ["safety", "policy"]:
            return _safety_policy_payload(config)
        if method == "POST" and parts == ["safety", "check-command"]:
            return _safety_check_command(config, payload)
        if method == "POST" and parts == ["safety", "check-path"]:
            return _safety_check_path(config, payload)
        if method == "POST" and parts == ["safety", "check-tool"]:
            return _safety_check_tool(config, payload)

        if method == "GET" and parts == ["proposals"]:
            status = (query.get("status") or [None])[0]
            return [_compact_proposal(item) for item in store.list_proposals(status=status, limit=_query_int(query, "limit", 100))]
        if len(parts) >= 2 and parts[0] == "proposals":
            return _proposal_api(method, parts, payload, store)

        if method == "GET" and parts == ["assets"]:
            return _list_assets(store, kind=(query.get("kind") or [None])[0])
        if len(parts) >= 2 and parts[0] == "assets":
            return _asset_api(method, parts, payload, store, config)

        if method == "GET" and parts == ["reflect", "reports"]:
            return _reflect_reports(config, limit=_query_int(query, "limit", 20))
        if method == "GET" and len(parts) == 3 and parts[:2] == ["reflect", "reports"]:
            return _reflect_report(config, parts[2])
        if method == "POST" and parts == ["reflect", "run"]:
            scope = ReflectScope(
                since=payload.get("since") if isinstance(payload.get("since"), str) else None,
                asset=payload.get("asset") if isinstance(payload.get("asset"), str) else None,
                modes=frozenset(payload.get("modes") or []),
                stale_days=payload.get("stale_days") if isinstance(payload.get("stale_days"), int) else None,
            )
            return ReflectEngine(config, store).run(scope, write_proposals=bool(payload.get("write_proposals", False)))

        if method == "GET" and parts == ["graph", "status"]:
            return store.graph_status()
        if method == "POST" and parts == ["graph", "rebuild"]:
            return store.rebuild_experience_graph()
        if method == "GET" and parts == ["graph", "explain"]:
            ref = (query.get("ref") or query.get("asset") or [None])[0]
            if not ref:
                raise GatewayError(400, "`ref` or `asset` is required")
            return store.graph_explain(ref, depth=_query_int(query, "depth", 2), limit=_query_int(query, "limit", 100))
        if method == "GET" and parts == ["graph", "view"]:
            ref = (query.get("ref") or query.get("asset") or [None])[0]
            if not ref:
                raise GatewayError(400, "`ref` or `asset` is required")
            return _graph_view(store, ref, depth=_query_int(query, "depth", 2), limit=_query_int(query, "limit", 100))

        if method == "GET" and parts == ["audit", "status"]:
            return build_project_audit_check(config, store, redaction="standard")
        if method == "POST" and parts == ["audit", "check"]:
            return build_project_audit_check(
                config,
                store,
                rebuild_graph=bool(payload.get("rebuild_graph", False)),
                strict=bool(payload.get("strict", False)),
                redaction=str(payload.get("redaction") or "standard"),
            )
        if method == "POST" and parts == ["audit", "bundle"]:
            return build_project_audit_bundle(
                config,
                store,
                limit_runs=int(payload.get("limit_runs") or 20),
                rebuild_graph=bool(payload.get("rebuild_graph", False)),
                redaction=str(payload.get("redaction") or "standard"),
                include_reflect=bool(payload.get("include_reflect", True)),
                reflect_limit=int(payload.get("reflect_limit") or 5),
            )

        if method == "GET" and parts == ["ci", "reports"]:
            return _ci_reports(config, limit=_query_int(query, "limit", 50))
        if method == "POST" and parts == ["ci", "reports"]:
            return _generate_ci_report(config, store, payload)
        if method == "POST" and len(parts) == 4 and parts[:2] == ["ci", "reports"] and parts[3] in {"publish-comment", "publish-pr-comment"}:
            return _publish_github_pr_comment(config, store, {**payload, "report_id": parts[2]})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["ci", "reports"]:
            return _ci_report(config, parts[2])
        if method == "GET" and parts == ["github", "context"]:
            return GitHubConnector(config).context()
        if method == "POST" and parts == ["github", "pr-comments"]:
            return _publish_github_pr_comment(config, store, payload)
        if method == "POST" and parts == ["github", "actions", "artifacts", "import"]:
            return _import_github_actions_artifacts(config, payload)
        if method == "GET" and parts == ["repos"]:
            return _multi_repo_status(config)

        if method == "GET" and parts == ["specs"]:
            return build_spec_context(config.paths.root)
        if method == "POST" and parts == ["spec", "check"]:
            spec = payload.get("spec")
            return check_spec_file(config.paths.root, str(spec).strip() if isinstance(spec, str) and spec.strip() else None)
        if method == "POST" and parts == ["spec", "verify"]:
            run_id = str(payload.get("run_id") or "latest")
            trajectory = store.latest_trajectory() if run_id == "latest" else store.get_trajectory(run_id)
            if not trajectory:
                raise GatewayError(404, "Run not found")
            specs = _string_list(payload.get("specs") or payload.get("spec"))
            return verify_spec_compliance(config.paths.root, trajectory, specs or None)

        raise GatewayError(404, f"No route for {method} /api{'/' + '/'.join(parts) if parts else ''}")

    def _run_jobs_api(
        self,
        method: str,
        parts: list[str],
        query: dict[str, list[str]],
        payload: dict[str, Any],
    ) -> Any:
        if method == "GET" and parts == ["runs", "jobs"]:
            return self.jobs.list()
        if method == "POST" and parts == ["runs", "jobs"]:
            job = self.jobs.start(payload)
            return job.snapshot()
        if len(parts) < 3:
            raise GatewayError(404, "Run job route not found")
        job_id = _safe_id(parts[2], "job_id")
        job = self.jobs.get(job_id)
        if not job:
            raise GatewayError(404, "Run job not found")
        if method == "GET" and len(parts) == 3:
            return job.snapshot(after=_query_nonnegative_int(query, "after", 0))
        if method == "GET" and len(parts) == 4 and parts[3] == "events":
            after = _query_nonnegative_int(query, "after", 0)
            if (query.get("format") or ["sse"])[0] == "json":
                return job.snapshot(after=after)
            return SSEStream(job, after=after, timeout_seconds=_query_int(query, "timeout", 60))
        if method == "POST" and len(parts) == 4 and parts[3] in {"cancel", "stop"}:
            reason = str(payload.get("reason") or "manual cancellation").strip()
            event = job.request_stop(reason)
            return {"job": job.snapshot(include_events=False), "event": event}
        raise GatewayError(404, "Run job route not found")

    def _chat_api(self, method: str, parts: list[str], payload: dict[str, Any], config: Config, store: ExperienceStore) -> Any:
        if method == "GET" and parts == ["chat", "sessions"]:
            return _list_chat_sessions(config)
        if method == "POST" and parts == ["chat", "sessions"]:
            return _create_chat_session(config, payload)
        if len(parts) < 3:
            raise GatewayError(404, "Chat session route not found")
        session_id = _safe_id(parts[2], "session_id")
        if method == "GET" and len(parts) == 3:
            return _load_chat_session(config, session_id)
        if method == "POST" and len(parts) == 4 and parts[3] == "message":
            session = _load_chat_session(config, session_id)
            task = payload.get("task") or payload.get("content")
            if not isinstance(task, str) or not task.strip():
                raise GatewayError(400, "`task` or `content` is required")
            _append_chat_message(session, role="user", content=task)
            result = _run_task(config, store, {**payload, "task": task, "session_id": session_id})
            _append_chat_message(
                session,
                role="assistant",
                content=str(result.get("summary") or result.get("status") or "Run finished."),
                run_id=result.get("task_id"),
                governance_summary=result.get("governance_summary") if isinstance(result.get("governance_summary"), dict) else {},
                tool_calls=result.get("actions") if isinstance(result.get("actions"), list) else [],
            )
            linked_runs = session.setdefault("linked_runs", [])
            if result.get("task_id") and result["task_id"] not in linked_runs:
                linked_runs.append(result["task_id"])
            session["updated_at"] = utc_now()
            _save_chat_session(config, session)
            return {"session": session, "run": result}
        if method == "POST" and len(parts) == 4 and parts[3] == "message-async":
            session = _load_chat_session(config, session_id)
            task = payload.get("task") or payload.get("content")
            if not isinstance(task, str) or not task.strip():
                raise GatewayError(400, "`task` or `content` is required")
            _append_chat_message(session, role="user", content=task)
            job = self.jobs.start({**payload, "task": task, "session_id": session_id}, session_id=session_id)
            session["active_job_id"] = job.job_id
            session["updated_at"] = utc_now()
            _save_chat_session(config, session)
            return {"session": session, "job": job.snapshot()}
        if method == "POST" and len(parts) == 4 and parts[3] == "retry":
            session = _load_chat_session(config, session_id)
            last_user = _last_user_message(session)
            if not last_user:
                raise GatewayError(400, "No user message to retry")
            task = str(last_user.get("content") or "").strip()
            _append_chat_message(
                session,
                role="user",
                content=task,
                metadata={"retry_of": last_user.get("message_id")},
            )
            result = _run_task(config, store, {**payload, "task": task, "session_id": session_id})
            _append_chat_message(
                session,
                role="assistant",
                content=str(result.get("summary") or result.get("status") or "Retry finished."),
                run_id=result.get("task_id"),
                governance_summary=result.get("governance_summary") if isinstance(result.get("governance_summary"), dict) else {},
                tool_calls=result.get("actions") if isinstance(result.get("actions"), list) else [],
                metadata={"retry": True},
            )
            linked_runs = session.setdefault("linked_runs", [])
            if result.get("task_id") and result["task_id"] not in linked_runs:
                linked_runs.append(result["task_id"])
            session["updated_at"] = utc_now()
            _save_chat_session(config, session)
            return {"session": session, "run": result}
        if method == "POST" and len(parts) == 4 and parts[3] == "retry-async":
            session = _load_chat_session(config, session_id)
            last_user = _last_user_message(session)
            if not last_user:
                raise GatewayError(400, "No user message to retry")
            task = str(last_user.get("content") or "").strip()
            _append_chat_message(
                session,
                role="user",
                content=task,
                metadata={"retry_of": last_user.get("message_id")},
            )
            job = self.jobs.start({**payload, "task": task, "session_id": session_id}, session_id=session_id)
            session["active_job_id"] = job.job_id
            session["updated_at"] = utc_now()
            _save_chat_session(config, session)
            return {"session": session, "job": job.snapshot()}
        if method == "POST" and len(parts) == 4 and parts[3] == "stop":
            session = _load_chat_session(config, session_id)
            active_job_id = str(session.get("active_job_id") or "").strip()
            active_job = self.jobs.get(active_job_id) if active_job_id else None
            if active_job:
                stop_event = active_job.request_stop(str(payload.get("reason") or "web console stop button").strip())
                session.setdefault("stop_requests", []).append(stop_event)
                _append_chat_message(
                    session,
                    role="assistant",
                    content=f"Stop requested for background job {active_job.job_id}. Praxile will cancel at the next safe runtime boundary.",
                    metadata={"stop_request": stop_event, "job_id": active_job.job_id},
                )
                session["updated_at"] = utc_now()
                _save_chat_session(config, session)
                return {"session": session, "stopped": True, "job": active_job.snapshot(include_events=False), "stop_request": stop_event}
            stop_request = {
                "requested_at": utc_now(),
                "reason": str(payload.get("reason") or "manual stop request").strip(),
                "status": "recorded",
                "note": "The stdlib gateway currently runs tasks synchronously; active cancellation is reserved for the async runner.",
            }
            session.setdefault("stop_requests", []).append(stop_request)
            _append_chat_message(
                session,
                role="assistant",
                content="Stop requested. No async run was attached to this session, so the request was recorded for audit.",
                metadata={"stop_request": stop_request},
            )
            session["updated_at"] = utc_now()
            _save_chat_session(config, session)
            return {"session": session, "stopped": False, "stop_request": stop_request}
        raise GatewayError(404, "Chat session route not found")


def _api_routes() -> list[str]:
    return [
        "GET /api/status",
        "GET /api/config",
        "GET /api/chat/sessions",
        "POST /api/chat/sessions",
        "GET /api/chat/sessions/{session_id}",
        "POST /api/chat/sessions/{session_id}/message",
        "POST /api/chat/sessions/{session_id}/message-async",
        "POST /api/chat/sessions/{session_id}/retry",
        "POST /api/chat/sessions/{session_id}/retry-async",
        "POST /api/chat/sessions/{session_id}/stop",
        "GET /api/runs",
        "POST /api/runs",
        "GET /api/runs/jobs",
        "POST /api/runs/jobs",
        "GET /api/runs/jobs/{job_id}",
        "GET /api/runs/jobs/{job_id}/events",
        "POST /api/runs/jobs/{job_id}/cancel",
        "GET /api/runs/{run_id}",
        "GET /api/runs/{run_id}/explain",
        "GET /api/runs/{run_id}/trajectory",
        "GET /api/runs/{run_id}/reward",
        "GET /api/runs/{run_id}/evidence",
        "GET /api/runs/{run_id}/artifacts",
        "GET /api/runs/{run_id}/silent-failures",
        "GET /api/models/providers",
        "POST /api/models/providers",
        "PATCH /api/models/providers/{provider_id}",
        "GET /api/models/roles",
        "PATCH /api/models/roles/{role}",
        "POST /api/models/test",
        "POST /api/models/test-all",
        "GET /api/channels",
        "POST /api/channels/bind",
        "POST /api/channels/{binding_id}/unbind",
        "GET /api/tools",
        "GET /api/safety/policy",
        "POST /api/safety/check-command",
        "POST /api/safety/check-path",
        "POST /api/safety/check-tool",
        "GET /api/proposals",
        "GET /api/proposals/{proposal_id}",
        "POST /api/proposals/{proposal_id}/edit",
        "POST /api/proposals/{proposal_id}/accept",
        "POST /api/proposals/{proposal_id}/reject",
        "GET /api/assets",
        "GET /api/assets/{asset_path}",
        "GET /api/assets/{asset_path}/usage",
        "GET /api/assets/{asset_path}/graph",
        "POST /api/assets/{asset_path}/archive",
        "POST /api/assets/{asset_path}/deprecate",
        "POST /api/assets/{asset_path}/reactivate",
        "GET /api/reflect/reports",
        "GET /api/reflect/reports/{reflect_id}",
        "POST /api/reflect/run",
        "GET /api/graph/status",
        "GET /api/graph/explain",
        "GET /api/graph/view",
        "POST /api/graph/rebuild",
        "GET /api/audit/status",
        "POST /api/audit/check",
        "POST /api/audit/bundle",
        "GET /api/ci/reports",
        "POST /api/ci/reports",
        "POST /api/ci/reports/{report_id}/publish-comment",
        "GET /api/ci/reports/{report_id}",
        "GET /api/github/context",
        "POST /api/github/pr-comments",
        "POST /api/github/actions/artifacts/import",
        "GET /api/repos",
        "GET /api/specs",
        "POST /api/spec/check",
        "POST /api/spec/verify",
    ]


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return max(1, int((query.get(key) or [default])[0]))
    except (TypeError, ValueError):
        return default


def _query_nonnegative_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return max(0, int((query.get(key) or [default])[0]))
    except (TypeError, ValueError):
        return default


def _status_payload(config: Config, store: ExperienceStore) -> dict[str, Any]:
    proposals = store.list_proposals(status=None, limit=1000)
    pending = [item for item in proposals if item.get("status") == "pending"]
    latest = store.latest_trajectory()
    return {
        "status": "ok",
        "agent": "praxile",
        "project": {
            "root": str(config.paths.root),
            "state": str(config.paths.state),
        },
        "counts": {
            "runs": len(store.list_history(limit=1000)),
            "pending_proposals": len(pending),
            "providers": len(config.get("model_providers", default={}) or {}),
            "model_roles": len(config.get("model_roles", default={}) or {}),
        },
        "latest_run": _compact_run(latest) if latest else None,
    }


def _doctor_payload(config: Config, store: ExperienceStore) -> dict[str, Any]:
    providers = _model_providers(config)
    roles = _model_roles(config)
    return {
        "project_root": str(config.paths.root),
        "state_exists": config.paths.state.exists(),
        "config_exists": config.paths.config.exists(),
        "providers": providers,
        "role_status_counts": _count_by(roles, "status"),
        "index": store.index_status(scan=False),
    }


def _sanitized_config(config: Config) -> dict[str, Any]:
    data = copy.deepcopy(config.data)
    providers = data.get("model_providers", {})
    if isinstance(providers, dict):
        for provider in providers.values():
            if isinstance(provider, dict):
                provider.pop("api_key", None)
                env_name = str(provider.get("api_key_env") or "")
                if env_name:
                    provider["api_key_env_status"] = "configured" if os.environ.get(env_name) else "missing"
    return data


def _model_providers(config: Config) -> list[dict[str, Any]]:
    providers = config.get("model_providers", default={}) or {}
    result: list[dict[str, Any]] = []
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        env_name = str(provider.get("api_key_env") or "")
        base_url = str(provider.get("base_url") or "")
        provider_type = str(provider.get("type") or "openai_compatible")
        local_endpoint = "localhost" in base_url or "127.0.0.1" in base_url or provider_type == "ollama"
        result.append(
            {
                "provider_id": provider_id,
                "type": provider_type,
                "base_url": base_url,
                "api_key_env": env_name or None,
                "api_key_status": "not_required" if local_endpoint and not os.environ.get(env_name) else ("configured" if env_name and os.environ.get(env_name) else "missing"),
                "models": [item.get("name", item) if isinstance(item, dict) else item for item in provider.get("models", [])],
                "timeout_seconds": provider.get("timeout_seconds"),
            }
        )
    if not result:
        result.append(
            {
                "provider_id": "local",
                "type": "local",
                "base_url": None,
                "api_key_env": None,
                "api_key_status": "not_required",
                "models": ["local_hash"],
                "timeout_seconds": None,
            }
        )
    return result


def _model_roles(config: Config) -> list[dict[str, Any]]:
    roles_config = config.get("model_roles", default={}) or {}
    providers = {item["provider_id"]: item for item in _model_providers(config)}
    rows: list[dict[str, Any]] = []
    for role, category, mode, purpose in MODEL_ROLE_CATALOG:
        role_config = roles_config.get(role, {}) if isinstance(roles_config, dict) else {}
        if not isinstance(role_config, dict):
            role_config = {}
        provider = role_config.get("provider")
        model = role_config.get("model")
        enabled = role_config.get("enabled", True)
        fallback = role_config.get("fallback", [])
        status = "disabled" if enabled is False else "not_configured"
        if provider == "local" and model == "local_hash":
            status = "connected"
        elif isinstance(provider, str) and isinstance(model, str) and provider and model:
            provider_row = providers.get(provider)
            if not provider_row:
                status = "provider_missing"
            elif provider_row.get("api_key_status") == "missing":
                status = "missing_key"
            else:
                status = "configured"
        rows.append(
            {
                "role": role,
                "category": category,
                "purpose": purpose,
                "mode": str(role_config.get("mode") or mode),
                "provider": provider,
                "model": model,
                "fallback": fallback if isinstance(fallback, list) else [],
                "status": status,
            }
        )
    return rows


def _role_catalog() -> dict[str, dict[str, str]]:
    return {
        role: {"category": category, "mode": mode, "purpose": purpose}
        for role, category, mode, purpose in MODEL_ROLE_CATALOG
    }


def _safe_config_id(value: str, label: str) -> str:
    if not re.match(r"^[A-Za-z0-9_.-]{1,96}$", value or ""):
        raise GatewayError(400, f"Invalid {label}")
    return value


def _require_confirm(payload: dict[str, Any], action: str) -> None:
    if not payload.get("confirm"):
        raise GatewayError(400, f"`confirm` is required to {action}")


def _payload_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if value is None:
        return default
    return bool(value)


def _upsert_model_provider(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "update model provider configuration")
    if payload.get("api_key"):
        raise GatewayError(400, "Do not send raw API keys. Store secrets in environment variables and set `api_key_env`.")
    provider_id = _safe_config_id(str(payload.get("provider_id") or "").strip(), "provider_id")
    providers = config.data.setdefault("model_providers", {})
    existing = providers.get(provider_id) if isinstance(providers.get(provider_id), dict) else {}
    provider = copy.deepcopy(existing)
    for key in ["type", "base_url", "api_key_env"]:
        if key in payload:
            value = payload.get(key)
            provider[key] = str(value).strip() if value is not None else None
    if "timeout_seconds" in payload:
        raw_timeout = payload.get("timeout_seconds")
        if raw_timeout in {None, ""}:
            provider["timeout_seconds"] = None
        else:
            try:
                provider["timeout_seconds"] = int(raw_timeout)
            except (TypeError, ValueError) as exc:
                raise GatewayError(400, "timeout_seconds must be an integer") from exc
    if "models" in payload:
        provider["models"] = _string_list(payload.get("models"))
    provider.setdefault("type", "openai_compatible")
    if provider.get("type") == "local":
        provider.setdefault("models", ["local_hash"])
    providers[provider_id] = provider
    config.write()
    updated = Config.load(config.paths.root)
    for row in _model_providers(updated):
        if row["provider_id"] == provider_id:
            return row
    raise GatewayError(500, "Provider update was written but could not be reloaded")


def _update_model_role(config: Config, role: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "update model role configuration")
    role = _safe_config_id(role, "role")
    catalog = _role_catalog()
    if role not in catalog:
        raise GatewayError(400, f"Unknown model role: {role}")
    roles = config.data.setdefault("model_roles", {})
    current = roles.get(role) if isinstance(roles.get(role), dict) else {}
    next_role = copy.deepcopy(current)
    if "enabled" in payload:
        next_role["enabled"] = _payload_bool(payload["enabled"], default=True)
    if "provider" in payload:
        next_role["provider"] = str(payload.get("provider") or "").strip() or None
    if "model" in payload:
        next_role["model"] = str(payload.get("model") or "").strip() or None
    if "mode" in payload:
        mode = str(payload.get("mode") or "").strip()
        if mode not in {"required", "recommended", "optional", "disabled"}:
            raise GatewayError(400, "mode must be required, recommended, optional, or disabled")
        next_role["mode"] = mode
        if mode == "disabled":
            next_role["enabled"] = False
    if "fallback" in payload:
        fallback = payload.get("fallback")
        next_role["fallback"] = _string_list(fallback)
    roles[role] = {key: value for key, value in next_role.items() if value is not None}
    config.write()
    updated = Config.load(config.paths.root)
    for row in _model_roles(updated):
        if row["role"] == role:
            return row
    raise GatewayError(500, "Role update was written but could not be reloaded")


def _test_model_role(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    role = _safe_config_id(str(payload.get("role") or "").strip(), "role")
    timeout = payload.get("timeout_seconds") if isinstance(payload.get("timeout_seconds"), int) else None
    if timeout is None:
        timeout = int(config.get("runtime", "online_check_timeout_seconds", default=8) or 8)
    role_row = next((item for item in _model_roles(config) if item["role"] == role), None)
    if not role_row:
        raise GatewayError(404, "Model role not found")
    targets = _role_route_targets(config, role)
    if targets:
        router = ModelRouter(config)
        return {"role": role, "routes": [_check_route_target(router, target, timeout_seconds=timeout) for target in targets]}
    return {"role": role, "routes": [], "status": role_row["status"], "detail": "No configured route target for this role"}


def _role_route_targets(config: Config, role: str) -> list[str]:
    roles = config.get("model_roles", default={}) or {}
    role_config = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(role_config, dict):
        return []
    targets: list[str] = []
    provider = role_config.get("provider")
    model = role_config.get("model")
    if isinstance(provider, str) and isinstance(model, str) and provider and model:
        targets.append(f"{provider}:{model}")
    for fallback in role_config.get("fallback") or []:
        if isinstance(fallback, str) and ":" in fallback:
            targets.append(fallback)
        elif isinstance(fallback, dict):
            fallback_provider = fallback.get("provider")
            fallback_model = fallback.get("model")
            if isinstance(fallback_provider, str) and isinstance(fallback_model, str):
                targets.append(f"{fallback_provider}:{fallback_model}")
    return list(dict.fromkeys(targets))


def _check_route_target(router: ModelRouter, target: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
    provider_name, model = target.split(":", 1) if ":" in target else ("", target)
    started = time.monotonic()
    result: dict[str, Any] = {
        "target": target,
        "provider": provider_name,
        "model": model,
        "provider_known": provider_name in router.providers or provider_name == "local",
        "timeout_seconds": timeout_seconds,
    }
    if provider_name == "local" and model == "local_hash":
        return {
            **result,
            "status": "ok",
            "detail": "local_hash is available without a network model endpoint",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    provider = router.providers.get(provider_name)
    if provider is None:
        return {
            **result,
            "status": "error",
            "detail": f"unknown provider: {provider_name or '(missing)'}",
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    if not model:
        return {**result, "status": "error", "detail": "missing model name", "latency_ms": int((time.monotonic() - started) * 1000)}
    try:
        provider.chat(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Reply with OK only."},
                    {"role": "user", "content": "OK?"},
                ],
                "temperature": 0,
                "max_tokens": 8,
                "timeout": timeout_seconds,
            }
        )
        status = "ok"
        detail = "model endpoint accepted a minimal chat request"
    except ModelUnavailable as exc:
        status = "unavailable"
        detail = str(exc)
    except ModelError as exc:
        status = "error"
        detail = str(exc)
    except Exception as exc:  # pragma: no cover - defensive network envelope guard
        status = "error"
        detail = f"{exc.__class__.__name__}: {exc}"
    return {
        **result,
        "status": status,
        "detail": detail,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def _channels_payload(config: Config) -> dict[str, Any]:
    system = ChannelSystem(config)
    bindings = []
    for binding in system.list_bindings():
        row = binding.to_dict()
        env_name = row.get("token_env")
        row["token_env_status"] = "configured" if isinstance(env_name, str) and os.environ.get(env_name) else "missing"
        bindings.append(row)
    return {
        "enabled": bool(config.get("gateway", "channels_enabled", default=False)),
        "default": config.get("channels", "default", default=None),
        "supported_platforms": sorted(SUPPORTED_PLATFORMS),
        "default_token_envs": dict(DEFAULT_TOKEN_ENVS),
        "bindings": bindings,
    }


def _bind_channel(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "bind a channel")
    platform = str(payload.get("platform") or "").strip()
    channel_id = str(payload.get("channel_id") or "").strip()
    if platform not in SUPPORTED_PLATFORMS or not channel_id:
        raise GatewayError(400, "`platform` and `channel_id` are required")
    try:
        binding = ChannelSystem(config).bind(
            platform,
            channel_id,
            guild_id=payload.get("guild_id"),
            thread_id=payload.get("thread_id"),
            name=payload.get("name"),
            kind=str(payload.get("kind") or "home"),
            mode=str(payload.get("mode") or "notify"),
            token_env=payload.get("token_env"),
            require_mention=_payload_bool(payload.get("require_mention"), default=True),
            allow_free_response=_payload_bool(payload.get("allow_free_response"), default=False),
            auto_thread=payload.get("auto_thread"),
            skill=payload.get("skill"),
            prompt=payload.get("prompt"),
            project_scope=str(payload.get("project_scope") or "current"),
            make_default=_payload_bool(payload.get("default"), default=False),
        )
    except ValueError as exc:
        raise GatewayError(400, str(exc)) from exc
    return binding.to_dict()


def _unbind_channel(config: Config, binding_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "unbind a channel")
    try:
        return ChannelSystem(config).unbind(binding_id).to_dict()
    except ValueError as exc:
        raise GatewayError(404, str(exc)) from exc


def _tool_catalog(config: Config) -> dict[str, Any]:
    registry = ToolRegistry(config)
    try:
        tools = []
        for item in registry.describe():
            name = str(item.get("name") or "")
            tools.append(
                {
                    **item,
                    "read_only": name in READ_ONLY_ACTIONS,
                    "requires_write_approval": name in {"edit_file", "run_command", "browser_screenshot"},
                    "safety_layer": _tool_safety_layer(name),
                }
            )
    finally:
        registry.close()
    return {
        "tools": tools,
        "read_only_actions": sorted(READ_ONLY_ACTIONS),
        "batch_read_only_only": True,
        "browser_enabled": bool(config.get("browser", "enabled", default=False)),
        "max_readonly_concurrency": int(config.get("executors", "max_readonly_concurrency", default=8) or 8),
    }


def _tool_safety_layer(name: str) -> str:
    if name in {"read_file", "read_files", "search", "edit_file"}:
        return "filesystem path policy + project safety rules"
    if name == "run_command":
        return "command allowlist policy + project safety rules"
    if name.startswith("browser_"):
        return "browser host allowlist policy + project safety rules"
    if name == "batch":
        return "read-only action gate + per-tool safety rules"
    return "runtime action schema + project safety rules"


def _safety_policy_payload(config: Config) -> dict[str, Any]:
    safety = SafetyPolicy(config)
    return {
        "sensitive_globs": config.get("safety", "sensitive_globs", default=[]) or [],
        "dangerous_command_patterns": config.get("safety", "dangerous_command_patterns", default=[]) or [],
        "allowed_command_prefixes": config.get("safety", "allowed_command_prefixes", default=[]) or [],
        "protected_paths": sorted({".praxile", *(config.get("safety", "protected_paths", default=[]) or [])}),
        "policy_files": config.get("safety", "policy_files", default=[]) or [],
        "policy_rules_count": len(safety.policy_rules),
        "policy_status": safety.policy_status(),
        "shell": {
            "allow_shell_features": bool(config.get("shell", "allow_shell_features", default=False)),
            "safe_mode_tee_pipe": True,
        },
        "browser": {
            "enabled": bool(config.get("browser", "enabled", default=False)),
            "allowed_hosts": config.get("browser", "allowed_hosts", default=[]) or [],
            "artifact_dir": config.get("browser", "artifact_dir", default=".praxile/experience/artifacts"),
        },
        "runtime": {
            "default_test_commands": config.get("runtime", "default_test_commands", default=[]) or [],
            "max_steps": config.get("runtime", "max_steps", default=None),
        },
    }


def _safety_check_command(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    command = payload.get("command")
    if not isinstance(command, str):
        raise GatewayError(400, "`command` is required")
    decision = SafetyPolicy(config).check_command(command)
    return {
        "command": command,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "risk_level": decision.risk_level,
    }


def _safety_check_path(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    path = payload.get("path")
    if not isinstance(path, str):
        raise GatewayError(400, "`path` is required")
    decision = SafetyPolicy(config).check_path(path, write=bool(payload.get("write", False)))
    return {
        "path": path,
        "write": bool(payload.get("write", False)),
        "allowed": decision.allowed,
        "reason": decision.reason,
        "risk_level": decision.risk_level,
    }


def _safety_check_tool(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    tool = payload.get("tool", payload.get("type"))
    if not isinstance(tool, str) or not tool:
        raise GatewayError(400, "`tool` is required")
    args = payload.get("args")
    if args is None:
        args = {key: value for key, value in payload.items() if key not in {"tool", "type", "context"}}
    if not isinstance(args, dict):
        raise GatewayError(400, "`args` must be an object")
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    decision = SafetyPolicy(config).check_tool_call(tool, args, context=context)
    return {
        "tool": tool,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "risk_level": decision.risk_level,
    }


def _run_task(
    config: Config,
    store: ExperienceStore,
    payload: dict[str, Any],
    *,
    cancel_requested: Any | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    task = payload.get("task")
    if not isinstance(task, str) or not task.strip():
        raise GatewayError(400, "`task` is required")
    test_commands = _string_list(payload.get("test_commands"))
    spec_files = _string_list(payload.get("spec_files") or payload.get("spec"))
    workspace_mode = str(payload.get("workspace_mode") or "in-place")
    if workspace_mode not in {"in-place", "", "none"}:
        raise GatewayError(400, "Web console currently supports workspace_mode=in-place only")
    run_config = Config(copy.deepcopy(config.data), ProjectPaths(config.paths.root))
    overrides = payload.get("model_role_overrides")
    if isinstance(overrides, dict):
        for role, target in overrides.items():
            if isinstance(role, str) and isinstance(target, str) and ":" in target:
                provider, model = target.split(":", 1)
                run_config.data.setdefault("model_roles", {}).setdefault(role, {}).update({"provider": provider, "model": model})
    if isinstance(payload.get("model_role_override"), str) and ":" in payload["model_role_override"]:
        provider, model = payload["model_role_override"].split(":", 1)
        run_config.data.setdefault("model_roles", {}).setdefault("coding_agent", {}).update({"provider": provider, "model": model})
    if isinstance(payload.get("allow_shell"), bool):
        run_config.data.setdefault("shell", {})["allow_shell_features"] = bool(payload["allow_shell"])
    trajectory = AgentRuntime(run_config).run(
        task,
        test_commands=test_commands or None,
        max_steps=int(payload.get("max_steps")) if payload.get("max_steps") is not None else None,
        dry_run=bool(payload.get("dry_run", False)),
        spec_files=spec_files or None,
        cancel_requested=cancel_requested,
        progress_callback=progress_callback,
    )
    return _run_detail(trajectory, store)


def _run_api(method: str, parts: list[str], store: ExperienceStore) -> Any:
    run_id = parts[1]
    trajectory = store.latest_trajectory() if run_id == "latest" else store.get_trajectory(run_id)
    if not trajectory:
        raise GatewayError(404, "Run not found")
    task_id = str(trajectory.get("task_id") or run_id)
    if method == "GET" and len(parts) == 2:
        return _run_detail(trajectory, store)
    if method == "GET" and len(parts) == 3:
        if parts[2] == "trajectory":
            return trajectory
        if parts[2] == "reward":
            return trajectory.get("reward_report") or {}
        if parts[2] == "evidence":
            return {
                "task_id": task_id,
                "evidence": trajectory.get("evidence") or trajectory.get("evidence_items") or [],
                "experience_candidates": trajectory.get("experience_candidates") or [],
                "loaded_assets": trajectory.get("loaded_assets") or [],
                "silent_failure_signals": trajectory.get("silent_failure_signals") or [],
            }
        if parts[2] == "artifacts":
            return _run_artifacts(trajectory)
        if parts[2] == "silent-failures":
            return trajectory.get("silent_failure_signals") or []
        if parts[2] == "explain":
            return _run_explain(trajectory, store)
    raise GatewayError(404, "Run route not found")


def _run_detail(trajectory: dict[str, Any], store: ExperienceStore) -> dict[str, Any]:
    task_id = str(trajectory.get("task_id") or "")
    actions = trajectory.get("actions") if isinstance(trajectory.get("actions"), list) else []
    proposals = [
        store.find_proposal(candidate.get("proposal_id")) or candidate
        for candidate in trajectory.get("experience_candidates", [])
        if isinstance(candidate, dict)
    ]
    return {
        "task_id": task_id,
        "task": trajectory.get("user_task"),
        "status": (trajectory.get("result") or {}).get("status"),
        "summary": (trajectory.get("result") or {}).get("summary"),
        "reward": (trajectory.get("reward_report") or {}).get("overall"),
        "reward_report": trajectory.get("reward_report") or {},
        "governance_summary": {
            "reward": (trajectory.get("reward_report") or {}).get("overall"),
            "proposals": len(proposals),
            "silent_failure_signals": len(trajectory.get("silent_failure_signals") or []),
            "loaded_assets": len(trajectory.get("loaded_assets") or []),
        },
        "spec_context": trajectory.get("spec_context") or {},
        "loaded_assets": trajectory.get("loaded_assets") or [],
        "silent_failure_signals": trajectory.get("silent_failure_signals") or [],
        "proposals": [_compact_proposal(item) for item in proposals if item],
        "actions": [_compact_action(item) for item in actions],
        "diff_summary": trajectory.get("diff_summary") or {},
        "artifacts": _run_artifacts(trajectory),
        "started_at": trajectory.get("start_time"),
        "ended_at": trajectory.get("end_time"),
    }


def _run_artifacts(trajectory: dict[str, Any]) -> dict[str, Any]:
    actions = trajectory.get("actions") if isinstance(trajectory.get("actions"), list) else []
    commands: list[dict[str, Any]] = []
    diffs: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        action_type = str(action.get("action_type") or action.get("type") or "")
        input_data = action.get("input") if isinstance(action.get("input"), dict) else action.get("input_data") if isinstance(action.get("input_data"), dict) else {}
        row = {
            "step": action.get("step"),
            "type": action_type,
            "status": action.get("status") or observation.get("status"),
            "risk_level": action.get("risk_level") or observation.get("risk_level"),
            "summary": shorten(str(observation.get("output") or ""), 400),
        }
        timeline.append(row)
        if action_type in {"run_command", "run_test"}:
            command = input_data.get("command") or (observation.get("data") or {}).get("command")
            item = {
                **row,
                "command": command,
                "output": str(observation.get("output") or ""),
                "returncode": (observation.get("data") or {}).get("returncode"),
            }
            commands.append(item)
            if action_type == "run_test":
                tests.append(item)
        diff = (observation.get("data") or {}).get("diff")
        if diff:
            diffs.append(
                {
                    **row,
                    "path": (observation.get("data") or {}).get("path") or input_data.get("path"),
                    "diff": str(diff),
                }
            )
    diff_summary = trajectory.get("diff_summary") if isinstance(trajectory.get("diff_summary"), dict) else {}
    if diff_summary.get("diff"):
        diffs.append(
            {
                "step": "git",
                "type": "git_diff",
                "status": "success",
                "path": diff_summary.get("pathspec"),
                "summary": diff_summary.get("stat"),
                "diff": diff_summary.get("diff"),
            }
        )
    return {
        "task_id": trajectory.get("task_id"),
        "timeline": timeline,
        "commands": commands,
        "tests": tests,
        "diffs": diffs,
        "diff_summary": diff_summary,
        "reward": trajectory.get("reward_report") or {},
        "evidence": trajectory.get("evidence") or trajectory.get("evidence_items") or [],
    }


def _run_explain(trajectory: dict[str, Any], store: ExperienceStore) -> dict[str, Any]:
    task_id = str(trajectory.get("task_id") or "")
    return {
        "run": _run_detail(trajectory, store),
        "asset_usage": store.usage_for_task(task_id) if task_id else [],
        "graph": store.graph_explain(task_id, depth=2, limit=80) if task_id else {},
    }


def _graph_view(store: ExperienceStore, ref: str, *, depth: int = 2, limit: int = 100) -> dict[str, Any]:
    graph = store.graph_explain(ref, depth=depth, limit=limit)
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    start = (graph.get("start_node") or {}).get("node_id")
    node_ids = [str(node.get("node_id")) for node in nodes if node.get("node_id")]
    if not node_ids:
        graph["view"] = {"nodes": [], "edges": [], "legend": {}}
        return graph
    distances = _graph_distances(start or node_ids[0], node_ids, edges)
    layers: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        layer = min(4, distances.get(node_id, 4))
        layers.setdefault(layer, []).append(node)
    view_nodes: list[dict[str, Any]] = []
    width = 920
    height = max(320, 130 * max(1, len(layers)))
    for layer, items in sorted(layers.items()):
        x = 110 + layer * 185
        gap = height / (len(items) + 1)
        for index, node in enumerate(sorted(items, key=lambda item: str(item.get("title") or item.get("node_id") or "")), start=1):
            node_type = str(node.get("node_type") or "unknown")
            view_nodes.append(
                {
                    **node,
                    "x": x,
                    "y": int(gap * index),
                    "label": _graph_label(node),
                    "color": _graph_node_color(node_type),
                }
            )
    view_edges = [
        {
            **edge,
            "source": edge.get("source_node_id"),
            "target": edge.get("target_node_id"),
            "label": edge.get("relation_type"),
        }
        for edge in edges
    ]
    graph["view"] = {
        "width": width,
        "height": height,
        "nodes": view_nodes,
        "edges": view_edges,
        "legend": {
            "run": _graph_node_color("run"),
            "proposal": _graph_node_color("proposal"),
            "asset": _graph_node_color("asset"),
            "spec": _graph_node_color("spec"),
            "feedback": _graph_node_color("feedback"),
            "unknown": _graph_node_color("unknown"),
        },
    }
    return graph


def _graph_distances(start: str, node_ids: list[str], edges: list[dict[str, Any]]) -> dict[str, int]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("source_node_id") or "")
        target = str(edge.get("target_node_id") or "")
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    distances = {start: 0}
    frontier = [start]
    while frontier:
        current = frontier.pop(0)
        for next_id in sorted(adjacency.get(current, set())):
            if next_id not in distances:
                distances[next_id] = distances[current] + 1
                frontier.append(next_id)
    return distances


def _graph_label(node: dict[str, Any]) -> str:
    title = str(node.get("title") or node.get("ref_path") or node.get("node_id") or "")
    return shorten(title, 42)


def _graph_node_color(node_type: str) -> str:
    return {
        "run": "#2563eb",
        "proposal": "#d97706",
        "asset": "#16a34a",
        "spec": "#7c3aed",
        "feedback": "#be123c",
    }.get(node_type, "#64748b")


def _proposal_api(method: str, parts: list[str], payload: dict[str, Any], store: ExperienceStore) -> Any:
    proposal_id = parts[1]
    proposal = store.find_proposal(proposal_id)
    if not proposal:
        raise GatewayError(404, "Proposal not found")
    if method == "GET" and len(parts) == 2:
        return proposal
    if method == "POST" and len(parts) == 3 and parts[2] == "edit":
        return _edit_proposal(proposal_id, payload, store)
    if method == "POST" and len(parts) == 3 and parts[2] == "accept":
        if not payload.get("confirm"):
            raise GatewayError(400, "`confirm` is required to accept a proposal")
        pending = store.find_proposal(proposal_id, status="pending")
        if not pending:
            raise GatewayError(404, "No pending proposal found")
        accepted = store.apply_proposal(pending)
        return accepted
    if method == "POST" and len(parts) == 3 and parts[2] == "reject":
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise GatewayError(400, "`reason` is required to reject a proposal")
        pending = store.find_proposal(proposal_id, status="pending")
        if not pending:
            raise GatewayError(404, "No pending proposal found")
        return store.reject_proposal(pending, reason=reason)
    raise GatewayError(404, "Proposal route not found")


def _edit_proposal(proposal_id: str, payload: dict[str, Any], store: ExperienceStore) -> dict[str, Any]:
    if not payload.get("confirm"):
        raise GatewayError(400, "`confirm` is required to edit a proposal")
    pending = store.find_proposal(proposal_id, status="pending")
    if not pending:
        raise GatewayError(404, "No pending proposal found")
    raw = payload.get("proposal")
    if isinstance(raw, str):
        try:
            edited = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GatewayError(400, f"Invalid proposal JSON: {exc}") from exc
    elif isinstance(raw, dict):
        edited = copy.deepcopy(raw)
    else:
        edited = copy.deepcopy(pending)
        for key in ["title", "reason", "evidence", "changes", "risk_level", "confidence", "target_files"]:
            if key in payload:
                edited[key] = payload[key]
    if not isinstance(edited, dict):
        raise GatewayError(400, "`proposal` must be an object")
    if edited.get("proposal_id") not in {None, proposal_id, pending.get("proposal_id")}:
        raise GatewayError(400, "Edited proposal_id must match the pending proposal")
    if edited.get("status") not in {None, "pending"}:
        raise GatewayError(400, "Only pending proposals can be edited through the web console")
    changes = edited.get("changes")
    if changes is not None and not isinstance(changes, list):
        raise GatewayError(400, "`changes` must be a list")
    edited["proposal_id"] = pending["proposal_id"]
    edited["status"] = "pending"
    for key in ["created_at", "source_task_id", "generated_by"]:
        if pending.get(key) is not None:
            edited[key] = pending[key]
    if not edited.get("target_files") and isinstance(changes, list):
        edited["target_files"] = [str(change.get("path")) for change in changes if isinstance(change, dict) and change.get("path")]
    events = pending.get("user_edits") if isinstance(pending.get("user_edits"), list) else []
    edited["user_edits"] = [
        *events,
        {
            "edited_at": utc_now(),
            "edited_by": "web_console",
            "reason": str(payload.get("reason") or "manual web console edit").strip(),
        },
    ]
    store.write_proposal(edited)
    return store.find_proposal(proposal_id, status="pending") or edited


def _asset_api(method: str, parts: list[str], payload: dict[str, Any], store: ExperienceStore, config: Config) -> Any:
    if len(parts) >= 3 and parts[-1] in {"usage", "graph", "archive", "deprecate", "reactivate"}:
        asset_path = _normalize_asset_path("/".join(parts[1:-1]))
        action = parts[-1]
        if method == "GET" and action == "usage":
            return {
                "path": asset_path,
                "usage_history": store.attribution_history_for_asset(asset_path, limit=50),
            }
        if method == "GET" and action == "graph":
            return store.graph_explain(asset_path, depth=2, limit=100)
        if method == "POST" and action in {"archive", "deprecate", "reactivate"}:
            return _asset_lifecycle_action(store, asset_path, action, payload)
    asset_path = _normalize_asset_path("/".join(parts[1:]))
    asset = store.get_asset(asset_path)
    if method == "GET" and asset:
        target = config.paths.root / asset_path
        content = ""
        if target.exists() and path_is_relative_to(target, config.paths.root):
            content = target.read_text(encoding="utf-8", errors="replace")[:30000]
        return {
            **asset,
            "content": content,
            "usage_history": store.attribution_history_for_asset(asset_path, limit=20),
            "graph": store.graph_explain(asset_path, depth=1, limit=60),
        }
    if not asset:
        raise GatewayError(404, "Asset not found")
    raise GatewayError(404, "Asset route not found")


def _asset_lifecycle_action(store: ExperienceStore, asset_path: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, f"{action} an asset")
    asset = store.get_asset(asset_path)
    if not asset:
        raise GatewayError(404, "Asset not found")
    status = {"archive": "archived", "deprecate": "deprecated", "reactivate": "active"}[action]
    reason = str(payload.get("reason") or f"manual web console {action}").strip()
    replaced_by = payload.get("replaced_by")
    if replaced_by is not None:
        replaced_by = _normalize_asset_path(str(replaced_by))
    return store.update_asset_status(
        asset_path,
        status=status,
        replaced_by=replaced_by,
        reason=reason,
        source="web_console",
    )


def _list_assets(store: ExperienceStore, *, kind: str | None = None) -> list[dict[str, Any]]:
    kinds = [kind] if kind else ["memory", "skill", "rule", "eval", "failure", "pattern"]
    assets: dict[str, dict[str, Any]] = {}
    for item_kind in kinds:
        if not item_kind:
            continue
        for asset in store.list_assets(item_kind, include_inactive=True):
            path = str(asset.get("path") or "")
            if path:
                assets[path] = _compact_asset(asset)
    return sorted(assets.values(), key=lambda item: str(item.get("path") or ""))


def _reflect_reports(config: Config, *, limit: int = 20) -> list[dict[str, Any]]:
    root = config.paths.state / "experience" / "reflect"
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        if not path_is_relative_to(path.resolve(), root.resolve()):
            continue
        data = read_json(path, {})
        if not isinstance(data, dict):
            continue
        findings = data.get("findings") if isinstance(data.get("findings"), list) else []
        generated = data.get("generated_proposals") if isinstance(data.get("generated_proposals"), list) else []
        written = data.get("written_proposal_paths") if isinstance(data.get("written_proposal_paths"), list) else []
        rows.append(
            {
                "reflect_id": data.get("reflect_id") or path.stem,
                "path": str(path.relative_to(config.paths.root)),
                "created_at": data.get("created_at"),
                "scope": data.get("scope") or {},
                "finding_count": len(findings),
                "generated_proposal_count": len(generated),
                "written_proposal_count": len(written),
                "summary": data.get("summary") or {},
            }
        )
    return rows[: max(1, int(limit or 20))]


def _reflect_report(config: Config, reflect_id: str) -> dict[str, Any]:
    safe_id = _safe_id(reflect_id, "reflect_id") if reflect_id != "latest" else reflect_id
    reports = _reflect_reports(config, limit=1000)
    if safe_id == "latest":
        if not reports:
            raise GatewayError(404, "Reflect report not found")
        return _load_reflect_report(config, reports[0]["path"])
    for row in reports:
        if row.get("reflect_id") == safe_id or Path(str(row.get("path") or "")).stem == safe_id:
            return _load_reflect_report(config, str(row["path"]))
    raise GatewayError(404, "Reflect report not found")


def _load_reflect_report(config: Config, relative_path: str) -> dict[str, Any]:
    path = (config.paths.root / relative_path).resolve()
    root = (config.paths.state / "experience" / "reflect").resolve()
    if not path_is_relative_to(path, root) or not path.exists():
        raise GatewayError(404, "Reflect report not found")
    data = read_json(path, {})
    if not isinstance(data, dict):
        raise GatewayError(500, "Reflect report is invalid")
    data.setdefault("path", str(path.relative_to(config.paths.root)))
    return data


def _ci_report_roots(config: Config) -> list[Path]:
    return [
        config.paths.state / "experience" / "ci",
        config.paths.state / "experience" / "reflect" / "ci",
        config.paths.state / "experience" / "audit",
    ]


def _ci_reports(config: Config, *, limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in _ci_report_roots(config):
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            if not path_is_relative_to(path.resolve(), root_resolved):
                continue
            data = read_json(path, {})
            if not isinstance(data, dict):
                continue
            rows.append(
                {
                    "report_id": _ci_report_id(config, path),
                    "path": str(path.relative_to(config.paths.root)),
                    "kind": data.get("kind") or data.get("audit_type") or data.get("report_type") or root.name,
                    "status": data.get("status") or data.get("conclusion") or data.get("result") or "unknown",
                    "summary": data.get("summary") or data.get("title") or data.get("message") or "",
                    "created_at": data.get("created_at") or data.get("generated_at"),
                    "run_id": data.get("run_id") or data.get("task_id"),
                    "pr": data.get("pull_request") or data.get("pr"),
                }
            )
    return rows[: max(1, int(limit or 50))]


def _ci_report(config: Config, report_id: str) -> dict[str, Any]:
    safe_id = _safe_id(report_id, "report_id") if report_id != "latest" else report_id
    reports = _ci_reports(config, limit=1000)
    if safe_id == "latest":
        if not reports:
            raise GatewayError(404, "CI/PR report not found")
        return _load_relative_json(config, str(reports[0]["path"]))
    for row in reports:
        if row.get("report_id") == safe_id or Path(str(row.get("path") or "")).stem == safe_id:
            return _load_relative_json(config, str(row["path"]))
    raise GatewayError(404, "CI/PR report not found")


def _generate_ci_report(config: Config, store: ExperienceStore, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "generate a CI/PR report artifact")
    run_id = str(payload.get("run_id") or "latest")
    trajectory = store.latest_trajectory() if run_id == "latest" else store.get_trajectory(run_id)
    if not trajectory:
        raise GatewayError(404, "Run not found")
    audit = build_project_audit_check(
        config,
        store,
        rebuild_graph=bool(payload.get("rebuild_graph", False)),
        strict=bool(payload.get("strict", False)),
        redaction=str(payload.get("redaction") or "standard"),
    )
    report_id = new_id("ci")
    report = {
        "report_id": report_id,
        "kind": "github_actions_pr_report",
        "status": _ci_status_from_run(trajectory, audit),
        "summary": (trajectory.get("result") or {}).get("summary") or "Praxile CI report generated.",
        "created_at": utc_now(),
        "run_id": trajectory.get("task_id"),
        "task": trajectory.get("user_task"),
        "reward": (trajectory.get("reward_report") or {}).get("overall"),
        "regression_passed": (trajectory.get("reward_report") or {}).get("regression_passed"),
        "proposal_count": len(trajectory.get("experience_candidates") or []),
        "silent_failure_count": len(trajectory.get("silent_failure_signals") or []),
        "audit": audit,
        "github": _github_context(),
        "comment_publishing": {
            "supported": True,
            "requires_confirm": True,
            "endpoint": "/api/github/pr-comments",
            "token_env": config.get("github", "token_env", default="GITHUB_TOKEN"),
        },
    }
    root = config.paths.state / "experience" / "ci"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{report_id}.json"
    write_json(path, report)
    if _payload_bool(payload.get("write_step_summary"), default=False):
        _write_github_step_summary(report)
    report["path"] = str(path.relative_to(config.paths.root))
    return report


def _publish_github_pr_comment(config: Config, store: ExperienceStore, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "publish a GitHub PR comment")
    connector = GitHubConnector(config)
    try:
        repo = connector.repository(payload.get("repository") if isinstance(payload.get("repository"), str) else None)
        if not repo:
            raise GitHubIntegrationError("GitHub repository is required")
        raw_pr = payload.get("pr_number") or connector.default_pr_number()
        if raw_pr is None:
            raise GitHubIntegrationError("GitHub PR number is required")
        pr_number = int(raw_pr)
        report_id = str(payload.get("report_id") or "latest")
        report = _ci_report(config, report_id) if not payload.get("body") else {}
        body = str(payload.get("body") or "").strip()
        if not body:
            marker = str(payload.get("marker") or config.get("github", "comment_marker", default="<!-- praxile-report -->") or "<!-- praxile-report -->")
            body = build_pr_comment_body(report, marker=marker)
        preview_only = _payload_bool(payload.get("preview_only"), default=False)
        result = {
            "repository": repo,
            "pr_number": pr_number,
            "report_id": report.get("report_id") or report_id,
            "preview_only": preview_only,
            "body": body,
        }
        if preview_only:
            result["status"] = "preview"
            return result
        published = connector.create_pr_comment(repo=repo, pr_number=pr_number, body=body)
        result.update(published)
        _record_github_comment_publish(config, result)
        return result
    except GitHubIntegrationError as exc:
        raise GatewayError(400, str(exc)) from exc


def _record_github_comment_publish(config: Config, result: dict[str, Any]) -> None:
    root = config.paths.state / "experience" / "ci" / "github-comments"
    root.mkdir(parents=True, exist_ok=True)
    record = {key: value for key, value in result.items() if key != "body"}
    record["body_preview"] = shorten(str(result.get("body") or ""), 1000)
    record["created_at"] = utc_now()
    write_json(root / f"{new_id('gh_comment')}.json", record)


def _import_github_actions_artifacts(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    _require_confirm(payload, "import GitHub Actions artifacts")
    try:
        return import_actions_artifacts(config, payload)
    except GitHubIntegrationError as exc:
        raise GatewayError(400, str(exc)) from exc


def _ci_status_from_run(trajectory: dict[str, Any], audit: dict[str, Any]) -> str:
    if (trajectory.get("result") or {}).get("status") == "failed":
        return "failed"
    if (trajectory.get("reward_report") or {}).get("regression_passed") is False:
        return "failed"
    if audit.get("status") in {"failed", "error"}:
        return "failed"
    if (trajectory.get("result") or {}).get("status") == "needs_human":
        return "needs_human"
    return "passed"


def _github_context() -> dict[str, Any]:
    keys = [
        "GITHUB_ACTIONS",
        "GITHUB_REPOSITORY",
        "GITHUB_REF",
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_EVENT_NAME",
        "GITHUB_HEAD_REF",
        "GITHUB_BASE_REF",
    ]
    return {key.lower(): os.environ.get(key) for key in keys if os.environ.get(key)}


def _write_github_step_summary(report: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    path = Path(summary_path)
    lines = [
        "## Praxile Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Run: `{report.get('run_id')}`",
        f"- Reward: `{report.get('reward')}`",
        f"- Proposals: `{report.get('proposal_count')}`",
        f"- Silent failures: `{report.get('silent_failure_count')}`",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def _ci_report_id(config: Config, path: Path) -> str:
    rel = path.relative_to(config.paths.state).as_posix()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", rel.rsplit(".", 1)[0]).strip("_")
    return slug[:96] or path.stem


def _load_relative_json(config: Config, relative_path: str) -> dict[str, Any]:
    path = (config.paths.root / relative_path).resolve()
    if not path_is_relative_to(path, config.paths.state.resolve()) or not path.exists():
        raise GatewayError(404, "Report not found")
    data = read_json(path, {})
    if not isinstance(data, dict):
        raise GatewayError(500, "Report is invalid")
    data.setdefault("path", str(path.relative_to(config.paths.root)))
    return data


def _multi_repo_status(config: Config) -> dict[str, Any]:
    roots = _configured_repo_roots(config)
    repos = []
    for root in roots:
        state = root / ".praxile"
        trajectories = state / "experience" / "trajectories"
        pending = state / "experience" / "proposals" / "pending"
        reports = state / "experience" / "ci"
        repos.append(
            {
                "root": str(root),
                "name": root.name,
                "current": root == config.paths.root,
                "state_exists": state.exists(),
                "config_exists": (state / "config.json").exists(),
                "runs": len(list(trajectories.rglob("*.json"))) if trajectories.exists() else 0,
                "pending_proposals": len(list(pending.rglob("*.json"))) if pending.exists() else 0,
                "ci_reports": len(list(reports.rglob("*.json"))) if reports.exists() else 0,
            }
        )
    return {"current_root": str(config.paths.root), "repos": repos}


def _configured_repo_roots(config: Config) -> list[Path]:
    roots: list[Path] = [config.paths.root]
    for item in config.get("gateway", "multi_repo_roots", default=[]) or []:
        candidate = (config.paths.root / str(item)).resolve() if not Path(str(item)).is_absolute() else Path(str(item)).resolve()
        if (candidate / ".praxile").exists():
            roots.append(candidate)
    if len(roots) == 1:
        parent = config.paths.root.parent
        try:
            candidates = sorted(parent.iterdir())[:50]
        except OSError:
            candidates = []
        for candidate in candidates:
            if candidate == config.paths.root or not candidate.is_dir():
                continue
            if (candidate / ".praxile" / "config.json").exists():
                roots.append(candidate.resolve())
            if len(roots) >= 24:
                break
    return list(dict.fromkeys(roots))


def _compact_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": row.get("task_id"),
        "task": row.get("user_task"),
        "status": row.get("status"),
        "reward": row.get("reward_score"),
        "trajectory_path": row.get("trajectory_path"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _compact_run(trajectory: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": trajectory.get("task_id"),
        "task": trajectory.get("user_task"),
        "status": (trajectory.get("result") or {}).get("status"),
        "reward": (trajectory.get("reward_report") or {}).get("overall"),
        "summary": (trajectory.get("result") or {}).get("summary"),
    }


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
    return {
        "step": action.get("step"),
        "type": action.get("action_type"),
        "status": action.get("status"),
        "risk_level": action.get("risk_level") or observation.get("risk_level"),
        "input": action.get("input") or {},
        "observation": {
            "status": observation.get("status"),
            "output": str(observation.get("output") or "")[:5000],
            "data": observation.get("data") or {},
        },
    }


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "title": proposal.get("title"),
        "status": proposal.get("status"),
        "risk_level": proposal.get("risk_level"),
        "confidence": proposal.get("confidence"),
        "confidence_level": proposal.get("confidence_level"),
        "recommended_action": (proposal.get("review_recommendation") or {}).get("recommended_action"),
        "source_task_id": proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id"),
        "generated_by": proposal.get("generated_by"),
        "target_files": proposal.get("target_files") or [],
        "evidence_summary": proposal.get("evidence_summary"),
        "proposal_gate": proposal.get("proposal_gate") or {},
        "created_at": proposal.get("created_at"),
        "updated_at": proposal.get("updated_at"),
    }


def _compact_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": asset.get("path"),
        "type": asset.get("type"),
        "title": asset.get("title"),
        "status": asset.get("status", "active"),
        "confidence": asset.get("confidence"),
        "usage_count": asset.get("usage_count", 0),
        "positive_outcome_count": asset.get("positive_outcome_count", 0),
        "negative_outcome_count": asset.get("negative_outcome_count", 0),
        "last_used_at": asset.get("last_used_at"),
        "source_task_id": asset.get("source_task_id"),
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_asset_path(value: str) -> str:
    text = unquote(str(value or "").strip())
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"


def _chat_root(config: Config) -> Path:
    root = config.paths.state / "experience" / "chat" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_id(value: str, label: str) -> str:
    if not re.match(r"^[A-Za-z0-9_-]{1,96}$", value or ""):
        raise GatewayError(400, f"Invalid {label}")
    return value


def _create_chat_session(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    session = {
        "session_id": new_id("sess"),
        "title": str(payload.get("title") or "New session"),
        "project_root": str(config.paths.root),
        "messages": [],
        "linked_runs": [],
        "created_at": now,
        "updated_at": now,
    }
    _save_chat_session(config, session)
    return session


def _list_chat_sessions(config: Config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_chat_root(config).glob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
        data = read_json(path, {})
        if isinstance(data, dict):
            rows.append(
                {
                    "session_id": data.get("session_id"),
                    "title": data.get("title"),
                    "linked_runs": data.get("linked_runs") or [],
                    "message_count": len(data.get("messages") or []),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                }
            )
    return rows


def _load_chat_session(config: Config, session_id: str) -> dict[str, Any]:
    path = _chat_root(config) / f"{session_id}.json"
    if not path.exists():
        raise GatewayError(404, "Chat session not found")
    data = read_json(path, {})
    if not isinstance(data, dict):
        raise GatewayError(500, "Chat session is invalid")
    return data


def _save_chat_session(config: Config, session: dict[str, Any]) -> None:
    session_id = _safe_id(str(session.get("session_id") or ""), "session_id")
    write_json(_chat_root(config) / f"{session_id}.json", session)


def _append_chat_message(
    session: dict[str, Any],
    *,
    role: str,
    content: str,
    run_id: str | None = None,
    governance_summary: dict[str, Any] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.setdefault("messages", []).append(
        {
            "message_id": new_id("msg"),
            "role": role,
            "content": content,
            "run_id": run_id,
            "governance_summary": governance_summary or {},
            "tool_calls": tool_calls or [],
            "metadata": metadata or {},
            "created_at": utc_now(),
        }
    )


def _append_chat_job_result(config: Config, session_id: str, job_id: str, result: dict[str, Any]) -> None:
    session = _load_chat_session(config, session_id)
    _append_chat_message(
        session,
        role="assistant",
        content=str(result.get("summary") or result.get("status") or "Run finished."),
        run_id=result.get("task_id"),
        governance_summary=result.get("governance_summary") if isinstance(result.get("governance_summary"), dict) else {},
        tool_calls=result.get("actions") if isinstance(result.get("actions"), list) else [],
        metadata={"job_id": job_id, "async": True},
    )
    linked_runs = session.setdefault("linked_runs", [])
    if result.get("task_id") and result["task_id"] not in linked_runs:
        linked_runs.append(result["task_id"])
    if session.get("active_job_id") == job_id:
        session.pop("active_job_id", None)
    session["updated_at"] = utc_now()
    _save_chat_session(config, session)


def _append_chat_error(config: Config, session_id: str, job_id: str, error: str) -> None:
    session = _load_chat_session(config, session_id)
    if session.get("active_job_id") == job_id:
        session.pop("active_job_id", None)
    _append_chat_message(
        session,
        role="assistant",
        content=f"Run failed: {error}",
        metadata={"job_id": job_id, "async": True, "error": error},
    )
    session["updated_at"] = utc_now()
    _save_chat_session(config, session)


def _last_user_message(session: dict[str, Any]) -> dict[str, Any] | None:
    for message in reversed(session.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user" and str(message.get("content") or "").strip():
            return message
    return None


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

        def do_PATCH(self) -> None:
            self._handle("PATCH")

        def do_DELETE(self) -> None:
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            try:
                self._check_auth()
                parsed = urlparse(self.path)
                payload = self._read_json() if method in {"POST", "PATCH", "DELETE"} else {}
                result = app.dispatch(method, parsed.path, parse_qs(parsed.query), payload)
                if isinstance(result, ConsolePage):
                    html_response(self, 200, result)
                    return
                if isinstance(result, SSEStream):
                    sse_response(self, result)
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
