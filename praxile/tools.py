from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from .config import Config
from .browser import BrowserEnv
from .environment import FileSystemEnv, GitEnv, ShellEnv, TestEnv
from .security import SafetyPolicy


READ_ONLY_ACTIONS = {"list_files", "project_map", "list_dir", "find_files", "search", "read_file", "read_files"}


class ToolRegistry:
    """Safe tool dispatcher for Praxile runtime actions."""

    def __init__(
        self,
        config: Config,
        *,
        fs: FileSystemEnv | None = None,
        git: GitEnv | None = None,
        shell: ShellEnv | None = None,
        tests: TestEnv | None = None,
        browser: BrowserEnv | None = None,
    ):
        self.config = config
        self.safety = SafetyPolicy(config)
        self.fs = fs or FileSystemEnv(config, self.safety)
        self.git = git or GitEnv(config)
        self.shell = shell or ShellEnv(config, self.safety)
        self.tests = tests or TestEnv(config, self.shell)
        self.browser = browser or BrowserEnv(config)
        self.cancel_requested: Callable[[], bool] | None = None

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": "list_files", "description": "List project files excluding ignored directories."},
            {"name": "project_map", "description": "Show a cached project structure summary and high-risk path signals."},
            {"name": "list_dir", "description": "List one project directory on demand."},
            {"name": "find_files", "description": "Find project files by path/name substring."},
            {"name": "search", "description": "Search project text after the same sensitive-path checks used by read_file."},
            {"name": "read_file", "description": "Read a project-relative file after safety checks; supports start_line/end_line for long files."},
            {"name": "read_files", "description": "Read up to 20 project-relative files with per-file safety checks."},
            {"name": "batch", "description": "Run up to 8 read-only actions concurrently."},
            {"name": "browser_open", "description": "Open a URL with the optional Playwright browser adapter."},
            {"name": "browser_screenshot", "description": "Capture a screenshot artifact with the optional Playwright adapter."},
            {"name": "edit_file", "description": "Write a full project-relative file with backup."},
            {"name": "run_command", "description": "Run an allowed test/lint/build command."},
            {"name": "finish", "description": "Finish the task with completed, needs_human, or failed."},
        ]

    def execute(self, action: dict[str, Any], *, task_id: str, step: int) -> dict[str, Any]:
        action_type = action.get("type")
        if not isinstance(action_type, str) or not action_type:
            return {"status": "failure", "output": "action type is required", "data": {}, "risk_level": "low"}
        decision = self.safety.check_tool_call(action_type, action, context={"task_id": task_id, "step": step})
        if not decision.allowed:
            return {
                "status": "blocked",
                "output": decision.reason,
                "data": {
                    "tool": action_type,
                    "safety_layer": "tool_call_policy",
                },
                "risk_level": decision.risk_level,
            }
        if action_type == "batch":
            return self._run_async(self.execute_async(action, task_id=task_id, step=step))
        if action_type == "list_files":
            return self.fs.list_files().to_dict()
        if action_type == "project_map":
            return self.fs.project_map(refresh=bool(action.get("refresh", False))).to_dict()
        if action_type == "list_dir":
            return self.fs.list_dir(str(action.get("path", ".")), max_files=int(action.get("max_files", 100) or 100)).to_dict()
        if action_type == "find_files":
            return self.fs.find_files(str(action.get("query", "")), limit=int(action.get("limit", 80) or 80)).to_dict()
        if action_type == "search":
            return self.fs.search(str(action.get("pattern", ""))).to_dict()
        if action_type == "read_file":
            return self.fs.read_file(
                str(action.get("path", "")),
                max_chars=int(action.get("max_chars", 20000) or 20000),
                start_line=action.get("start_line"),
                end_line=action.get("end_line"),
            ).to_dict()
        if action_type == "read_files":
            raw_paths = action.get("paths", [])
            if not isinstance(raw_paths, list):
                return {"status": "failure", "output": "paths must be a list", "data": {}, "risk_level": "low"}
            paths = [str(path) for path in raw_paths]
            return self.fs.read_files(paths).to_dict()
        if action_type == "edit_file":
            return self.fs.write_file(
                str(action.get("path", "")),
                str(action.get("content", "")),
                task_id=task_id,
                step=step,
            ).to_dict()
        if action_type == "run_command":
            return self.shell.run(str(action.get("command", "")), cancel_requested=self.cancel_requested).to_dict()
        if action_type == "browser_open":
            return self.browser.open(str(action.get("url", ""))).to_dict()
        if action_type == "browser_screenshot":
            return self.browser.screenshot(str(action.get("url", "")), name=action.get("name")).to_dict()
        if action_type == "finish":
            return {"status": "success", "output": action.get("summary", "finished"), "data": {}, "risk_level": "low"}
        return {"status": "failure", "output": f"unknown action type: {action_type}", "data": {}, "risk_level": "low"}

    async def execute_async(self, action: dict[str, Any], *, task_id: str, step: int) -> dict[str, Any]:
        if action.get("type") != "batch":
            return await asyncio.to_thread(self.execute, action, task_id=task_id, step=step)
        raw_actions = action.get("actions", [])
        if not isinstance(raw_actions, list) or not raw_actions:
            return {"status": "failure", "output": "batch actions must be a non-empty list", "data": {}, "risk_level": "low"}
        max_concurrency = max(1, min(16, int(self.config.get("executors", "max_readonly_concurrency", default=8) or 8)))
        actions = [item for item in raw_actions[:max_concurrency] if isinstance(item, dict)]
        rejected = [
            item.get("type")
            for item in actions
            if item.get("type") not in READ_ONLY_ACTIONS
        ]
        if rejected:
            return {
                "status": "blocked",
                "output": f"batch only supports read-only actions; rejected: {', '.join(str(item) for item in rejected)}",
                "data": {"rejected_action_types": rejected},
                "risk_level": "medium",
            }
        coordinator = _executor_payload(
            str(action.get("executor_id") or "parallel_readonly"),
            kind="parallel_readonly_coordinator",
            role="read_only_batch",
        )
        prefix = str(self.config.get("executors", "readonly_executor_prefix", default="readonly_explorer") or "readonly_explorer")
        executor_events = [
            _executor_payload(
                f"{prefix}_{index + 1}",
                kind="readonly_worker",
                role=str(item.get("type") or "read_only"),
                parent_executor_id=coordinator["executor_id"],
            )
            for index, item in enumerate(actions)
        ]
        observations = await asyncio.gather(
            *[
                asyncio.to_thread(self._execute_with_executor, item, task_id=task_id, step=step + index, executor=executor_events[index])
                for index, item in enumerate(actions)
            ]
        )
        statuses = [item.get("status", "unknown") for item in observations]
        output = "\n\n".join(
            f"--- #{index + 1} {actions[index].get('type')} [{observations[index].get('status')}]\n"
            f"{observations[index].get('output', '')}"
            for index in range(len(observations))
        )
        status = "blocked" if statuses and all(item == "blocked" for item in statuses) else "success"
        if statuses and all(item == "failure" for item in statuses):
            status = "failure"
        return {
            "status": status,
            "output": output,
            "data": {
                "concurrent": True,
                "count": len(observations),
                "executor": coordinator,
                "executor_events": executor_events,
                "actions": actions,
                "observations": observations,
                "max_concurrency": max_concurrency,
            },
            "risk_level": "low",
        }

    def _execute_with_executor(
        self,
        action: dict[str, Any],
        *,
        task_id: str,
        step: int,
        executor: dict[str, Any],
    ) -> dict[str, Any]:
        observation = self.execute(action, task_id=task_id, step=step)
        data = observation.get("data")
        if not isinstance(data, dict):
            data = {}
            observation["data"] = data
        data["executor"] = executor
        return observation

    def _run_async(self, awaitable: Any) -> dict[str, Any]:
        timeout = max(1, int(self.config.get("runtime", "model_timeout_seconds", default=30) or 30))
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.run(asyncio.wait_for(awaitable, timeout=timeout))
            except (TimeoutError, asyncio.TimeoutError):
                return {
                    "status": "failure",
                    "output": f"async batch timed out after {timeout}s",
                    "data": {"timeout_seconds": timeout},
                    "risk_level": "low",
                }
        result: dict[str, Any] | None = None
        error: BaseException | None = None
        loop: asyncio.AbstractEventLoop | None = None

        def runner() -> None:
            nonlocal result, error, loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(asyncio.wait_for(awaitable, timeout=timeout))
            except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError) as exc:
                error = exc
            except BaseException as exc:  # pragma: no cover - defensive bridge
                error = exc
            finally:
                try:
                    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    asyncio.set_event_loop(None)
                    loop.close()

        thread = threading.Thread(target=runner, name="praxile-batch-loop", daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            if loop is not None:
                loop.call_soon_threadsafe(_cancel_loop_tasks, loop)
                thread.join(timeout=2.0)
        if thread.is_alive():
            return {
                "status": "failure",
                "output": f"async batch compatibility loop timed out after {timeout}s and did not stop cleanly",
                "data": {"timeout_seconds": timeout, "thread_alive": True},
                "risk_level": "low",
            }
        if isinstance(error, (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError)):
            return {
                "status": "failure",
                "output": f"async batch compatibility loop timed out after {timeout}s",
                "data": {"timeout_seconds": timeout},
                "risk_level": "low",
            }
        if error:
            return {
                "status": "failure",
                "output": f"async batch failed in compatibility loop: {error}",
                "data": {},
                "risk_level": "low",
            }
        return result or {
            "status": "failure",
            "output": "async batch returned no result",
            "data": {},
            "risk_level": "low",
        }

    def close(self) -> None:
        if hasattr(self.browser, "close"):
            self.browser.close()


def _cancel_loop_tasks(loop: asyncio.AbstractEventLoop) -> None:
    for task in asyncio.all_tasks(loop):
        task.cancel()


def _executor_payload(
    executor_id: str,
    *,
    kind: str,
    role: str,
    parent_executor_id: str | None = None,
) -> dict[str, Any]:
    payload = {
        "executor_id": executor_id,
        "kind": kind,
        "role": role,
    }
    if parent_executor_id:
        payload["parent_executor_id"] = parent_executor_id
    return payload
