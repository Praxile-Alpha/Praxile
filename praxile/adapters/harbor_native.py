"""Harbor plugin: praxile.adapters.harbor_native:PraxileAgent.

Install Harbor in a separate benchmark environment. Harbor, not this plugin,
owns container creation, termination, networking, and official verification.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import shlex
import tempfile
import time
from pathlib import Path, PurePosixPath

from harbor.agents.base import BaseAgent
from ..utils import new_id, write_json


class PraxileAgent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, *args, wheel_path: str, wheel_sha256: str,
                 workspace: str, base_url: str, max_steps: int = 50,
                 timeout_seconds: int = 900, credential_file: str | None = None, **kwargs):
        self.wheel = Path(wheel_path).resolve(strict=True)
        if not self.wheel.name.endswith(".whl") or hashlib.sha256(self.wheel.read_bytes()).hexdigest() != wheel_sha256:
            raise ValueError("Praxile wheel digest mismatch")
        path = PurePosixPath(workspace)
        if not path.is_absolute() or ".." in path.parts or str(path) != workspace or workspace == "/":
            raise ValueError("explicit absolute task workspace required")
        if not 1 <= max_steps <= 1000 or not 1 <= timeout_seconds <= 86400:
            raise ValueError("invalid execution budget")
        self.wheel_sha256 = wheel_sha256
        self.workspace, self.base_url = workspace, base_url
        self.max_steps, self.timeout_seconds = max_steps, timeout_seconds
        self.credential_file = Path(credential_file) if credential_file else None
        if self.credential_file and timeout_seconds > 3540:
            raise ValueError("broker-backed execution requires timeout_seconds <= 3540")
        self.remote_root = "/tmp/praxile-native-" + wheel_sha256[:16]
        super().__init__(*args, **kwargs)
        if not self.model_name:
            raise ValueError("model_name is required")
        if self.mcp_servers or self.skills_dir:
            raise ValueError("native benchmark plugin does not yet support Harbor MCP or skill injection")

    @staticmethod
    def name() -> str:
        return "praxile-native"

    def version(self) -> str:
        return "sha256:" + self.wheel_sha256

    async def setup(self, environment) -> None:
        remote_wheel = self.remote_root + "/" + self.wheel.name
        await self._exec(environment, shlex.join(["mkdir", "-p", self.remote_root]))
        await environment.upload_file(self.wheel, remote_wheel)
        verify = ("import hashlib,pathlib,sys; "
                  "assert sys.version_info >= (3,11), 'Python >=3.11 required'; "
                  f"assert hashlib.sha256(pathlib.Path({remote_wheel!r}).read_bytes()).hexdigest()=={self.wheel_sha256!r}")
        await self._exec(environment, shlex.join(["python3", "-c", verify]))
        await self._exec(environment, shlex.join(["python3", "-m", "venv", self.remote_root + "/venv"]))
        await self._exec(environment, shlex.join([self.remote_root + "/venv/bin/python", "-m", "pip",
                                                "install", "--no-index", "--no-deps", remote_wheel]))

    async def _exec(self, environment, command: str, *, timeout_seconds: int | None = None):
        result = await environment.exec(command, timeout_sec=timeout_seconds or self.timeout_seconds)
        if result.return_code != 0:
            # Do not echo process output that may contain task credentials.
            raise RuntimeError(f"native executor command failed: exit={result.return_code}")
        return result

    async def run(self, instruction, environment, context) -> None:
        if self.credential_file is None:
            return await self._run_with_endpoint(instruction, environment, context)
        from ..eval.credential_broker import ChatCredentialBroker, read_private_key
        broker = ChatCredentialBroker(key=read_private_key(self.credential_file),
            endpoint=self.base_url.rstrip("/") + "/chat/completions", model=self.model_name,
            max_calls=min(self.max_steps + 10, 1000), max_output=6000,
            output_budget=min((self.max_steps + 10) * 6000, 10_000_000),
            ttl_seconds=self.timeout_seconds + 60)
        remote = self.remote_root + "/" + new_id("capability")
        try:
            _, port = broker.start()
            with tempfile.TemporaryDirectory(prefix="praxile-broker-") as directory:
                capability = Path(directory) / "capability"
                capability.touch(mode=0o600)
                capability.write_text(broker.capability, encoding="utf-8")
                await environment.upload_file(capability, remote)
            if getattr(environment, "default_user", None) == "dev":
                ownership = await environment.exec(shlex.join(["chown", "dev", remote]), user="root", timeout_sec=5)
                if ownership.return_code != 0:
                    raise RuntimeError("could not assign temporary capability to task user")
            await self._exec(environment, shlex.join(["chmod", "600", remote]))
            await self._run_with_endpoint(instruction, environment, context,
                endpoint=f"http://host.docker.internal:{port}/v1", capability_file=remote)
        finally:
            broker.revoke()
            await asyncio.to_thread(broker.close)
            # A revoked leftover capability is unusable even if cleanup fails.
            try:
                await asyncio.wait_for(environment.exec(shlex.join(["rm", "-f", remote]), timeout_sec=5), timeout=6)
            except (Exception, asyncio.CancelledError):
                pass

    async def _run_with_endpoint(self, instruction, environment, context, *, endpoint=None, capability_file=None) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="praxile-public-instruction-") as directory:
            public = Path(directory) / "instruction.txt"
            public.write_text(instruction, encoding="utf-8")
            await environment.upload_file(public, self.remote_root + "/instruction.txt")
        cancel_file = self.remote_root + "/" + new_id("cancel")
        argv = [
            self.remote_root + "/venv/bin/python", "-m", "praxile.eval.process_supervisor",
            "--timeout", str(self.timeout_seconds), "--status", self.remote_root + "/supervision.json",
            "--cancel-file", cancel_file, "--",
            self.remote_root + "/venv/bin/python", "-m", "praxile.eval.native_worker",
            "--workspace", self.workspace, "--instruction", self.remote_root + "/instruction.txt",
            "--result", self.remote_root + "/result.json", "--model", self.model_name,
            "--base-url", endpoint or self.base_url, "--max-steps", str(self.max_steps),
        ]
        if capability_file:
            argv.extend(["--capability-file", capability_file])
        command = shlex.join(argv)
        started = time.monotonic()
        record = {"schema_version": "praxile.native_execution.v1", "status": "running",
                  "wheel_sha256": self.wheel_sha256, "result_available": False}
        write_json(self.logs_dir / "praxile-execution.json", record)
        try:
            await self._exec(environment, command, timeout_seconds=self.timeout_seconds + 10)
        except BaseException as error:
            record.update(status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                          error_type=type(error).__name__)
            if isinstance(error, asyncio.CancelledError):
                await self._cancel_worker(environment, cancel_file, record)
            # Failure evidence must not replace the primary execution exception.
            try:
                await asyncio.wait_for(self._collect_result(environment, context), timeout=15)
                record["result_available"] = True
            except (Exception, asyncio.CancelledError) as collection_error:
                record["collection_error_type"] = type(collection_error).__name__
            await self._record_supervision(environment, record)
            record["elapsed_seconds"] = time.monotonic() - started
            try:
                write_json(self.logs_dir / "praxile-execution.json", record)
            except OSError:
                pass
            raise
        try:
            await asyncio.wait_for(self._collect_result(environment, context), timeout=15)
            record.update(status="completed", result_available=True)
        except BaseException as error:
            record.update(status="collection_failed", error_type=type(error).__name__)
            raise
        finally:
            await self._record_supervision(environment, record)
            record["elapsed_seconds"] = time.monotonic() - started
            try:
                write_json(self.logs_dir / "praxile-execution.json", record)
            except OSError:
                if record["status"] == "completed":
                    raise

    async def _cancel_worker(self, environment, cancel_file, record) -> None:
        record["worker_cancel"] = {"requested": False, "termination_confirmed": False}
        try:
            result = await asyncio.wait_for(environment.exec(shlex.join([
                self.remote_root + "/venv/bin/python", "-c",
                f"from pathlib import Path; Path({cancel_file!r}).touch()"]), timeout_sec=5), timeout=6)
            if result.return_code != 0:
                raise RuntimeError("worker cancellation request failed")
            record["worker_cancel"]["requested"] = True
            async def confirm():
                while True:
                    await self._record_supervision(environment, record)
                    if record.get("worker_supervision", {}).get("status") in {
                            "completed", "failed", "cancelled", "timed_out"}:
                        record["worker_cancel"]["termination_confirmed"] = True
                        return
                    await asyncio.sleep(.1)
            await asyncio.wait_for(confirm(), timeout=6)
        except (Exception, asyncio.CancelledError) as error:
            record["worker_cancel"]["error_type"] = type(error).__name__

    async def _record_supervision(self, environment, record) -> None:
        temporary = self.logs_dir / "praxile-supervision.partial.json"
        record.pop("worker_supervision", None)
        try:
            await asyncio.wait_for(environment.download_file(
                self.remote_root + "/supervision.json", temporary), timeout=5)
            supervision = json.loads(temporary.read_text(encoding="utf-8"))
            if (not isinstance(supervision, dict)
                    or supervision.get("schema_version") != "praxile.worker_supervision.v1"
                    or supervision.get("status") not in {
                        "starting", "running", "completed", "failed", "timed_out", "cancelled", "supervisor_error"}):
                raise ValueError("invalid worker supervision report")
            record["worker_supervision"] = {
                "status": supervision["status"],
                "returncode": supervision.get("returncode") if type(supervision.get("returncode")) is int else None,
            }
        except Exception as error:
            record["supervision_collection_error_type"] = type(error).__name__
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    async def _collect_result(self, environment, context) -> None:
        # Never leave a truncated download at the canonical evidence path.
        temporary = self.logs_dir / "praxile-result.partial.json"
        try:
            await environment.download_file(self.remote_root + "/result.json", temporary)
            result = json.loads(temporary.read_text(encoding="utf-8"))
            if (not isinstance(result, dict) or result.get("schema_version") != "praxile.native_benchmark_run.v1"
                    or not isinstance(result.get("trajectory"), dict)
                    or result.get("experience_mode") != "fresh_disabled"):
                raise ValueError("invalid native worker result")
            temporary.replace(self.logs_dir / "praxile-result.json")
            self.populate_context_post_run(context)
        finally:
            temporary.unlink(missing_ok=True)

    def populate_context_post_run(self, context) -> None:
        result_path = self.logs_dir / "praxile-result.json"
        if not result_path.is_file():
            return
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("schema_version") != "praxile.native_benchmark_run.v1":
            raise ValueError("unsupported native worker result")
        trajectory = result["trajectory"]
        calls = trajectory.get("model_routing", {}).get("calls", [])
        # Missing usage stays unknown, rather than becoming a free run.
        for field, keys in (("n_input_tokens", ("prompt_tokens", "input_tokens")),
                            ("n_output_tokens", ("completion_tokens", "output_tokens"))):
            values = [next((call.get("usage", {})[key] for key in keys if key in call.get("usage", {})), None)
                      for call in calls]
            setattr(context, field, None)
            if values and all(type(value) is int and value >= 0 for value in values):
                setattr(context, field, sum(values))
        context.metadata = {**(context.metadata or {}), "praxile": {
            "wheel_sha256": self.wheel_sha256, "trajectory_task_id": trajectory.get("task_id"),
            "result": trajectory.get("result"), "official_evaluation": "external",
            "experience_mode": result["experience_mode"],
        }}


class WorkBuddyPraxileAgent(PraxileAgent):
    """Match WorkBuddy's non-root rollout without modifying upstream agents."""

    @staticmethod
    def name() -> str:
        return "praxile-workbuddy"

    async def setup(self, environment) -> None:
        if self.workspace != "/workspace" or getattr(environment, "default_user", None) != "dev":
            raise ValueError("WorkBuddy native executor requires /workspace and agent_user=dev")
        # This fixed setup command is control-plane code, not an agent tool.
        result = await environment.exec(
            "set -eu; if ! id dev >/dev/null 2>&1; then useradd -m -s /bin/bash dev; fi; "
            'test "$(id -u dev)" != 0; mkdir -p /workspace; chown -hR dev /workspace',
            user="root", cwd="/", timeout_sec=self.timeout_seconds,
        )
        if result.return_code != 0:
            raise RuntimeError("could not initialize WorkBuddy non-root execution user")
        await super().setup(environment)
