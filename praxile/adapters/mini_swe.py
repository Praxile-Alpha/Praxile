from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence, TextIO

from ..trace import AdapterCapabilities, AgentEvent, ArtifactRecord, RunHandle, TokenUsage
from ..utils import new_id, utc_now
from .v2 import (
    AGENT_ADAPTER_PROTOCOL_VERSION,
    AdapterPolicy,
    AdapterPolicyError,
    AdapterRunNotFound,
    AdapterTask,
    AdapterUnavailableError,
)
from .mini_swe_preflight import MiniSwePreflightReport, run_mini_swe_preflight
from .mini_swe_stopping import MiniSweStoppingDecision, MiniSweStoppingMonitor, MiniSweStoppingPolicy
from .trace_normalization import bounded_native_message


@dataclass
class _MiniSweRun:
    task: AdapterTask
    policy: AdapterPolicy
    process: subprocess.Popen[Any] | None
    preflight: MiniSwePreflightReport
    preflight_path: Path
    trajectory_path: Path
    stdout_path: Path
    stderr_path: Path
    stdout_handle: TextIO
    stderr_handle: TextIO
    started_at: str
    timeout_seconds: float
    events: list[AgentEvent] | None = None
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    cancelled: bool = False
    stopping_decision: MiniSweStoppingDecision | None = None


class MiniSweAgentAdapter:
    """AgentAdapter V2 bridge for the mini-SWE-agent v2 ``mini`` CLI."""

    name = "mini-swe-agent"
    protocol_version = AGENT_ADAPTER_PROTOCOL_VERSION

    def __init__(
        self,
        *,
        executable: str = "mini",
        command_prefix: Sequence[str] | None = None,
        model: str | None = None,
        model_class: str | None = None,
        config_specs: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
        default_timeout_seconds: float = 1800.0,
    ):
        self.executable = executable
        self.command_prefix = tuple(command_prefix) if command_prefix is not None else None
        self.model = model
        self.model_class = model_class
        self.config_specs = tuple(config_specs)
        self.environment = dict(environment or {})
        self.default_timeout_seconds = default_timeout_seconds
        self._runs: dict[str, _MiniSweRun] = {}

    def capabilities(self) -> AdapterCapabilities:
        available, resolved = self.availability()
        try:
            native_version = importlib.metadata.version("mini-swe-agent")
        except importlib.metadata.PackageNotFoundError:
            native_version = None
        return AdapterCapabilities(
            event_streaming=False,
            artifact_collection=True,
            context_injection=True,
            token_usage=True,
            cancellation=True,
            checkpointing=False,
            sandbox_visibility=False,
            subagent_visibility=False,
            native_event_types=("messages", "extra.actions", "info.model_stats"),
            metadata={
                "protocol_version": self.protocol_version,
                "adapter_version": "3",
                "native_runtime": "mini-swe-agent>=2,<3",
                "native_runtime_version": native_version,
                "stream_mode": "post_run_trajectory",
                "available": available,
                "executable": resolved,
            },
        )

    def availability(self) -> tuple[bool, str]:
        first = self.command_prefix[0] if self.command_prefix else self.executable
        resolved = shutil.which(first)
        if resolved:
            return True, resolved
        path = Path(first).expanduser()
        if path.is_file():
            return True, str(path.resolve())
        if self.command_prefix is None and len(Path(first).parts) == 1:
            sibling = Path(sys.executable).parent / first
            if sibling.is_file() and os.access(sibling, os.X_OK):
                return True, str(sibling)
        return False, first

    def run(self, task: AdapterTask, policy: AdapterPolicy) -> RunHandle:
        if not bool(policy.settings.get("allow_unattended_execution", False)):
            raise AdapterPolicyError("mini-SWE-agent subprocess runs require allow_unattended_execution=true")
        if not bool(policy.settings.get("workspace_isolated", False)):
            raise AdapterPolicyError("mini-SWE-agent yolo mode requires an explicitly isolated workspace")
        if not task.root.is_dir():
            raise AdapterPolicyError(f"project root does not exist: {task.root}")
        for spec in self.config_specs:
            candidate = Path(spec).expanduser()
            if candidate.is_file() and candidate.suffix != ".yaml":
                raise AdapterPolicyError(
                    f"mini-SWE-agent file config specs must use the .yaml suffix: {candidate}"
                )
        available, resolved = self.availability()
        if not available:
            raise AdapterUnavailableError(
                f"mini-SWE-agent executable not found: {resolved}; install the 'mini-swe' optional dependency"
            )
        try:
            timeout = float(policy.budgets.get("wall_timeout_seconds", self.default_timeout_seconds))
        except (TypeError, ValueError) as exc:
            raise AdapterPolicyError("wall_timeout_seconds must be numeric") from exc
        if timeout <= 0:
            raise AdapterPolicyError("wall_timeout_seconds must be greater than zero")

        native_run_id = new_id("mini-native")
        trace_id = str(task.metadata.get("trace_id") or new_id("trace"))
        run_id = str(task.metadata.get("run_id") or new_id("run"))
        run_root = task.root / ".praxile" / "trace" / "native" / run_id
        run_root.mkdir(parents=True, exist_ok=False)
        preflight_path = run_root / "preflight.json"
        trajectory_path = run_root / "trajectory.traj.json"
        stdout_path = run_root / "stdout.log"
        stderr_path = run_root / "stderr.log"
        stdout_handle = stdout_path.open("w", encoding="utf-8")
        stderr_handle = stderr_path.open("w", encoding="utf-8")
        preflight = run_mini_swe_preflight(
            task.root,
            settings=policy.settings,
            config_specs=self.config_specs,
        )
        preflight_path.write_text(
            json.dumps(preflight.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        command = self._command(task, policy, trajectory_path, resolved_executable=resolved)
        env = os.environ.copy()
        env.update(self.environment)
        if "MSWEA_GLOBAL_CONFIG_DIR" not in self.environment:
            env["MSWEA_GLOBAL_CONFIG_DIR"] = str(run_root / "mini-config")
        if "MSWEA_CONFIGURED" not in self.environment:
            env["MSWEA_CONFIGURED"] = "true"
        if "MSWEA_SILENT_STARTUP" not in self.environment:
            env["MSWEA_SILENT_STARTUP"] = "1"
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        process: subprocess.Popen[Any] | None = None
        if preflight.passed:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=task.root,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    env=env,
                    shell=False,
                    **kwargs,
                )
            except Exception:
                stdout_handle.close()
                stderr_handle.close()
                raise
        else:
            stdout_handle.close()
            stderr_handle.close()

        handle = RunHandle(
            adapter=self.name,
            native_run_id=native_run_id,
            trace_id=trace_id,
            run_id=run_id,
            task_id=task.task_id,
            metadata={
                "protocol_version": self.protocol_version,
                "native_runtime": "mini-swe-agent",
                "trajectory_uri": self._uri(task.root, trajectory_path),
                "stream_mode": "post_run_trajectory",
                "parent_run_id": task.metadata.get("parent_run_id"),
                "workspace_mode": preflight.workspace_mode,
                "workspace_root": preflight.workspace_root,
                "preflight_uri": self._uri(task.root, preflight_path),
            },
        )
        self._runs[native_run_id] = _MiniSweRun(
            task=task,
            policy=policy,
            process=process,
            preflight=preflight,
            preflight_path=preflight_path,
            trajectory_path=trajectory_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_handle=stdout_handle,
            stderr_handle=stderr_handle,
            started_at=utc_now(),
            timeout_seconds=timeout,
        )
        return handle

    def stream_events(self, run_handle: RunHandle) -> Iterator[AgentEvent]:
        state = self._state(run_handle)
        if state.events is None:
            state.events = self._finish_and_translate(run_handle, state)
        yield from state.events

    def get_artifacts(self, run_handle: RunHandle) -> list[ArtifactRecord]:
        state = self._state(run_handle)
        if state.events is None:
            raise AdapterPolicyError("stream_events() must complete before artifacts are collected")
        return list(state.artifacts)

    def cancel(self, run_handle: RunHandle) -> None:
        state = self._state(run_handle)
        if state.events is not None:
            return
        state.cancelled = True
        if state.process is not None:
            self._terminate(state.process)
        self._close_handles(state)

    def close(self) -> None:
        for state in self._runs.values():
            if state.process is not None and state.process.poll() is None:
                self._terminate(state.process)
            self._close_handles(state)

    def __enter__(self) -> "MiniSweAgentAdapter":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _command(
        self,
        task: AdapterTask,
        policy: AdapterPolicy,
        output_path: Path,
        *,
        resolved_executable: str | None = None,
    ) -> list[str]:
        command = list(self.command_prefix or (resolved_executable or self.executable,))
        command.extend(["--task", self._instruction(task, policy), "--output", str(output_path), "--yolo", "--exit-immediately"])
        model = str(policy.settings.get("model") or self.model or "").strip()
        if model:
            command.extend(["--model", model])
        model_class = str(policy.settings.get("model_class") or self.model_class or "").strip()
        if model_class:
            command.extend(["--model-class", model_class])
        effective_specs = list(self.config_specs or ("mini.yaml",))
        effective_specs.append(f"environment.cwd={task.root.resolve()}")
        for spec in effective_specs:
            command.extend(["--config", spec])
        command.extend(["--environment-class", "local"])
        step_limit = policy.settings.get("step_limit")
        if step_limit is not None:
            try:
                parsed_step_limit = int(step_limit)
            except (TypeError, ValueError) as exc:
                raise AdapterPolicyError("step_limit must be an integer") from exc
            if parsed_step_limit <= 0:
                raise AdapterPolicyError("step_limit must be greater than zero")
            command.extend(["--config", f"agent.step_limit={parsed_step_limit}"])
        max_cost = policy.budgets.get("max_cost")
        if max_cost is not None:
            command.extend(["--cost-limit", str(float(max_cost))])
        return command

    @staticmethod
    def _instruction(task: AdapterTask, policy: AdapterPolicy) -> str:
        root = task.root.resolve()
        parts = [
            task.instruction,
            (
                "<praxile_workspace_contract mode=\"local\">\n"
                f"The repository root is exactly: {root}\n"
                "Every command starts in that directory. Use repository-relative paths. "
                "Do not search for or switch to /testbed. Do not install project dependencies globally.\n"
                "The harness has already validated the workspace. Spend the bounded step budget on "
                "reproduction, the smallest evidence-backed repository change, and verification. "
                "If half the budget is consumed without a repository change, stop repeating environment "
                "or repository census commands and either implement the best-supported patch or finish "
                "with an explicit blocker.\n"
                "</praxile_workspace_contract>"
            ),
        ]
        if policy.context:
            serialized = json.dumps([dict(item) for item in policy.context], ensure_ascii=False, sort_keys=True)
            parts.append(
                "<praxile_context policy_id=\""
                f"{policy.policy_id}\" policy_version=\"{policy.version}\">\n{serialized}\n</praxile_context>"
            )
        return "\n\n".join(parts)

    def _finish_and_translate(self, handle: RunHandle, state: _MiniSweRun) -> list[AgentEvent]:
        timed_out = self._wait_for_process(state)

        trajectory: dict[str, Any] = {}
        parse_error: str | None = None
        if state.trajectory_path.is_file():
            try:
                value = json.loads(state.trajectory_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    trajectory = value
                else:
                    parse_error = "native trajectory root is not an object"
            except (OSError, json.JSONDecodeError) as exc:
                parse_error = str(exc)

        events = self._translate_messages(handle, state, trajectory)
        patch_path = state.trajectory_path.parent / "workspace.patch"
        if state.preflight.passed:
            self._capture_workspace_patch(state.task.root, patch_path)
        progress = self._progress(trajectory, state.task.root, patch_path)
        sequence = len(events)
        events.append(self._event(handle, sequence, "PROGRESS", "mini-swe-adapter", progress))
        sequence += 1
        if state.stopping_decision is not None:
            events.append(
                self._event(
                    handle,
                    sequence,
                    "STOP_DECISION",
                    "praxile-control-plane",
                    state.stopping_decision.to_dict(),
                )
            )
            sequence += 1
        for path, artifact_type, media_type in (
            (state.preflight_path, "adapter_preflight", "application/json"),
            (state.trajectory_path, "native_trajectory", "application/json"),
            (patch_path, "workspace_patch", "text/x-diff"),
            (state.stdout_path, "process_stdout", "text/plain"),
            (state.stderr_path, "process_stderr", "text/plain"),
        ):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            artifact_id = f"{handle.run_id}:mini:artifact:{len(state.artifacts)}"
            event = self._event(
                handle,
                sequence,
                "ARTIFACT_CHANGE",
                "mini-swe-adapter",
                {"artifact_type": artifact_type, "uri": self._uri(state.task.root, path)},
                artifact_ids=(artifact_id,),
            )
            events.append(event)
            state.artifacts.append(
                ArtifactRecord(
                    artifact_id=artifact_id,
                    trace_id=handle.trace_id,
                    run_id=handle.run_id,
                    type=artifact_type,
                    uri=self._uri(state.task.root, path),
                    content_digest=self._digest(path),
                    producer_event_id=event.event_id,
                    created_at=event.timestamp,
                    media_type=media_type,
                    size=path.stat().st_size,
                    metadata={"native_runtime": "mini-swe-agent"},
                )
            )
            sequence += 1

        info = trajectory.get("info", {}) if isinstance(trajectory.get("info"), Mapping) else {}
        native_status = str(info.get("exit_status") or "")
        if not state.preflight.passed:
            status = "failed"
            native_status = "PreflightFailed"
        elif state.cancelled:
            status = "cancelled"
        elif timed_out:
            status = "timed_out"
        elif state.stopping_decision is not None:
            status = "policy_stopped"
            native_status = "PraxileStoppingPolicy"
        elif state.process is not None and state.process.returncode == 0 and trajectory:
            status = "completed" if native_status in {"", "Submitted", "Success", "completed"} else "failed"
        else:
            status = "failed"
        final_payload = {
            "status": status,
            "native_exit_status": native_status,
            "submission": info.get("submission", ""),
            "process_returncode": state.process.returncode if state.process is not None else None,
            "preflight_status": state.preflight.status,
            "patch_created": progress["patch_created"],
        }
        if state.stopping_decision is not None:
            final_payload["stopping_decision"] = state.stopping_decision.to_dict()
        if parse_error:
            final_payload["trajectory_parse_error"] = parse_error
        events.append(self._event(handle, sequence, "FINAL_RESULT", "mini-swe-agent", final_payload))
        events.append(self._event(handle, sequence + 1, "RUN_END", "mini-swe-adapter", {"status": status}))
        return events

    def _translate_messages(
        self,
        handle: RunHandle,
        state: _MiniSweRun,
        trajectory: Mapping[str, Any],
    ) -> list[AgentEvent]:
        native_ref = self._uri(state.task.root, state.trajectory_path)
        events = [
            self._event(
                handle,
                0,
                "RUN_START",
                "mini-swe-adapter",
                {
                    "instruction": state.task.instruction,
                    "policy_id": state.policy.policy_id,
                    "policy_version": state.policy.version,
                    "native_trajectory_format": trajectory.get("trajectory_format"),
                },
                timestamp=state.started_at,
                native_payload_ref=native_ref,
            )
        ]
        sequence = 1
        events.append(
            self._event(
                handle,
                sequence,
                "PREFLIGHT",
                "mini-swe-adapter",
                state.preflight.to_dict(),
                native_payload_ref=self._uri(state.task.root, state.preflight_path),
            )
        )
        sequence += 1
        if state.policy.context:
            events.append(
                self._event(
                    handle,
                    sequence,
                    "CONTEXT_INJECT",
                    "praxile-control-plane",
                    {
                        "policy_id": state.policy.policy_id,
                        "policy_version": state.policy.version,
                        "items": [dict(item) for item in state.policy.context],
                        "budgets": dict(state.policy.budgets),
                        "settings": dict(state.policy.settings),
                    },
                )
            )
            sequence += 1

        messages = trajectory.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        pending_actions: list[tuple[str, dict[str, Any]]] = []
        raw_verification_commands = state.policy.settings.get("verification_commands", [])
        verification_commands = (
            {str(item) for item in raw_verification_commands if isinstance(item, str)}
            if isinstance(raw_verification_commands, (list, tuple))
            else set()
        )
        for index, raw_message in enumerate(messages):
            if not isinstance(raw_message, Mapping):
                continue
            message = dict(raw_message)
            role = str(message.get("role") or message.get("type") or "unknown")
            extra = message.get("extra", {})
            extra = dict(extra) if isinstance(extra, Mapping) else {}
            actions = self._actions(message, extra)
            rejected_model_response = extra.get("model_response")
            is_rejected_model_call = isinstance(rejected_model_response, str) and bool(rejected_model_response)
            if role == "assistant" or actions or is_rejected_model_call:
                usage = self._token_usage(message, extra)
                cost = self._float_or_none(extra.get("cost"))
                payload: dict[str, Any] = {
                    "message_index": index,
                    "native_message": bounded_native_message(message),
                }
                if is_rejected_model_call:
                    payload.update(
                        {
                            "parse_status": "rejected",
                            "parse_error_type": extra.get("interrupt_type", "FormatError"),
                            "model_response": bounded_native_message(
                                {"value": rejected_model_response}
                            )["value"],
                        }
                    )
                events.append(
                    self._event(
                        handle,
                        sequence,
                        "MODEL_CALL",
                        "mini-swe-agent:model",
                        payload,
                        token_usage=usage,
                        cost=cost,
                        native_payload_ref=native_ref,
                    )
                )
                sequence += 1
            for action_index, action in enumerate(actions):
                tool_call_id = str(
                    action.get("id") or action.get("tool_call_id") or f"{handle.run_id}:mini:call:{index}:{action_index}"
                )
                events.append(
                    self._event(
                        handle,
                        sequence,
                        "TOOL_CALL",
                        "mini-swe-agent",
                        {
                            "message_index": index,
                            "action": bounded_native_message(action),
                        },
                        tool_call_id=tool_call_id,
                        native_payload_ref=native_ref,
                    )
                )
                sequence += 1
                pending_actions.append((tool_call_id, action))
            is_observation = role in {"tool", "observation"} or bool(extra.get("tool_call_id"))
            if role == "user" and pending_actions and index > 1:
                is_observation = True
            if is_observation:
                pending_call_id, pending_action = pending_actions.pop(0) if pending_actions else ("", {})
                observed_call_id = str(extra.get("tool_call_id") or message.get("tool_call_id") or pending_call_id or "")
                events.append(
                    self._event(
                        handle,
                        sequence,
                        "TOOL_RESULT",
                        "mini-swe-agent:environment",
                        {
                            "message_index": index,
                            "native_message": bounded_native_message(message),
                        },
                        tool_call_id=observed_call_id or None,
                        native_payload_ref=native_ref,
                    )
                )
                sequence += 1
                command = str(pending_action.get("command") or "")
                if command in verification_commands:
                    returncode = self._observation_returncode(message, extra)
                    events.append(
                        self._event(
                            handle,
                            sequence,
                            "VERIFICATION",
                            "mini-swe-agent:environment",
                            {
                                "message_index": index,
                                "command": command,
                                "status": "passed" if returncode == 0 else "failed" if returncode is not None else "unknown",
                                "returncode": returncode,
                            },
                            tool_call_id=observed_call_id or None,
                            native_payload_ref=native_ref,
                        )
                    )
                    sequence += 1
        return events

    def _wait_for_process(self, state: _MiniSweRun) -> bool:
        if state.process is None:
            return False
        deadline = time.monotonic() + state.timeout_seconds
        policy = MiniSweStoppingPolicy.from_settings(state.policy.settings)
        monitor = MiniSweStoppingMonitor(policy)
        timed_out = False
        try:
            while state.process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate(state.process)
                    break
                if policy.enabled:
                    trajectory = self._read_partial_trajectory(state.trajectory_path)
                    if trajectory:
                        decision = monitor.evaluate(
                            trajectory,
                            patch_present=self._workspace_has_patch(state.task.root),
                        )
                        if decision is not None:
                            state.stopping_decision = decision
                            self._terminate(state.process)
                            break
                time.sleep(policy.poll_interval_seconds if policy.enabled else min(0.5, state.timeout_seconds))
            if state.process.poll() is None:
                self._terminate(state.process)
        finally:
            self._close_handles(state)
        return timed_out

    @staticmethod
    def _read_partial_trajectory(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _workspace_has_patch(root: Path) -> bool:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--",
                    ".",
                    ":(exclude).praxile",
                    ":(exclude).praxile/**",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    @classmethod
    def _progress(cls, trajectory: Mapping[str, Any], root: Path, patch_path: Path) -> dict[str, Any]:
        commands: list[str] = []
        messages = trajectory.get("messages", [])
        if isinstance(messages, list):
            for raw in messages:
                if not isinstance(raw, Mapping):
                    continue
                extra = raw.get("extra")
                extra = extra if isinstance(extra, Mapping) else {}
                for action in cls._actions(raw, extra):
                    command = action.get("command")
                    if isinstance(command, str) and command.strip():
                        commands.append(command.strip())
        normalized = [re.sub(r"\s+", " ", item).strip() for item in commands]
        seen: set[str] = set()
        repeated = 0
        first_write: int | None = None
        environment_probes = 0
        for index, command in enumerate(normalized, start=1):
            if command in seen:
                repeated += 1
            seen.add(command)
            if cls._is_environment_probe(command):
                environment_probes += 1
            if first_write is None and cls._is_likely_repo_write(command, root):
                first_write = index
        patch_created = patch_path.is_file() and patch_path.stat().st_size > 0
        return {
            "workspace_root": str(root.resolve()),
            "tool_action_count": len(commands),
            "environment_probe_count": environment_probes,
            "repeated_command_count": repeated,
            "first_repo_write_action_heuristic": first_write,
            "patch_created": patch_created,
            "patch_created_after_action": first_write if patch_created else None,
            "measurement_notes": [
                "write timing is inferred from shell command shape",
                "patch_created is measured from the final Git workspace diff",
            ],
        }

    @staticmethod
    def _is_environment_probe(command: str) -> bool:
        return bool(
            re.search(r"(^|[;&|]\s*)(pwd|git\s+rev-parse|which\s+python|python\s+--version)(\s|$)", command)
            or "/testbed" in command
        )

    @staticmethod
    def _is_likely_repo_write(command: str, root: Path) -> bool:
        if re.search(r"(?:^|\s)(?:/tmp|\$TMPDIR|\$\{TMPDIR\})(?:/|\s|$)", command):
            return False
        root_text = re.escape(str(root.resolve()))
        write_operator = rf"(?:>|>>|\btee\b|\bsed\s+-i\b|\bapply_patch\b|\bpatch\s+-p\d|\bgit\s+apply\b)"
        if not re.search(write_operator, command):
            return False
        absolute_targets = re.findall(r"(?:>|>>|\btee(?:\s+-a)?\s+)(/[^\s;&|]+)", command)
        if absolute_targets and not any(re.match(rf"^{root_text}(?:/|$)", item) for item in absolute_targets):
            return False
        return True

    @staticmethod
    def _actions(message: Mapping[str, Any], extra: Mapping[str, Any]) -> list[dict[str, Any]]:
        actions = extra.get("actions")
        if isinstance(actions, list):
            return [dict(item) for item in actions if isinstance(item, Mapping)]
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        result: list[dict[str, Any]] = []
        for item in tool_calls:
            if not isinstance(item, Mapping):
                continue
            action = dict(item)
            function = action.get("function")
            if isinstance(function, Mapping) and isinstance(function.get("arguments"), str):
                try:
                    action["function"] = {**dict(function), "arguments": json.loads(str(function["arguments"]))}
                except json.JSONDecodeError:
                    pass
            result.append(action)
        return result

    @staticmethod
    def _token_usage(message: Mapping[str, Any], extra: Mapping[str, Any]) -> TokenUsage | None:
        usage = extra.get("usage", message.get("usage"))
        if not isinstance(usage, Mapping):
            response = extra.get("response")
            if isinstance(response, Mapping):
                usage = response.get("usage")
        if not isinstance(usage, Mapping):
            return None
        return TokenUsage.from_dict(usage)

    def _state(self, handle: RunHandle) -> _MiniSweRun:
        state = self._runs.get(handle.native_run_id)
        if state is None or handle.adapter != self.name:
            raise AdapterRunNotFound(f"unknown mini-SWE-agent run: {handle.native_run_id}")
        return state

    @staticmethod
    def _capture_workspace_patch(root: Path, output_path: Path) -> None:
        index_path = output_path.parent / "workspace-patch.index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index_path)
        try:
            read_tree = subprocess.run(
                ["git", "-C", str(root), "read-tree", "HEAD"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                shell=False,
                env=env,
            )
            if read_tree.returncode != 0:
                return
            staged = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "add",
                    "-A",
                    "--",
                    ".",
                    ":(exclude).praxile/trace/native",
                    ":(exclude).praxile/trace/native/**",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                shell=False,
                env=env,
            )
            if staged.returncode != 0:
                return
            result = subprocess.run(
                ["git", "-C", str(root), "diff", "--cached", "--binary", "--no-ext-diff", "HEAD", "--"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                shell=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return
        finally:
            for candidate in (index_path, index_path.with_suffix(index_path.suffix + ".lock")):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
        if result.returncode == 0 and result.stdout:
            output_path.write_bytes(result.stdout)

    @staticmethod
    def _observation_returncode(message: Mapping[str, Any], extra: Mapping[str, Any]) -> int | None:
        for value in (extra.get("returncode"), message.get("returncode")):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        content = message.get("content")
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, Mapping):
                    value = parsed.get("returncode")
                    if isinstance(value, int) and not isinstance(value, bool):
                        return value
            except json.JSONDecodeError:
                pass
            match = re.search(r"<returncode>\s*(-?\d+)\s*</returncode>", content)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _event(
        handle: RunHandle,
        sequence: int,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ) -> AgentEvent:
        return AgentEvent.create(
            event_id=f"{handle.run_id}:mini:{sequence}",
            trace_id=handle.trace_id,
            run_id=handle.run_id,
            task_id=handle.task_id,
            parent_run_id=(str(handle.metadata["parent_run_id"]) if handle.metadata.get("parent_run_id") else None),
            type=event_type,
            actor=actor,
            payload=payload,
            backend_sequence=sequence,
            **kwargs,
        )

    @staticmethod
    def _terminate(process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass

    @staticmethod
    def _close_handles(state: _MiniSweRun) -> None:
        for handle in (state.stdout_handle, state.stderr_handle):
            if not handle.closed:
                handle.close()

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _uri(root: Path, path: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_uri()

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
