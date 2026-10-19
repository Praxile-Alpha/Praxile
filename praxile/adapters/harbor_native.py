"""Harbor plugin: praxile.adapters.harbor_native:PraxileAgent.

Install Harbor in a separate benchmark environment. Harbor, not this plugin,
owns container creation, termination, networking, and official verification.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from harbor.agents.base import BaseAgent


class PraxileAgent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, *args, wheel_path: str, wheel_sha256: str,
                 workspace: str, base_url: str, max_steps: int = 50,
                 timeout_seconds: int = 900, **kwargs):
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

    async def _exec(self, environment, command: str):
        result = await environment.exec(command, timeout_sec=self.timeout_seconds)
        if result.return_code != 0:
            # Do not echo process output that may contain task credentials.
            raise RuntimeError(f"native executor command failed: exit={result.return_code}")
        return result

    async def run(self, instruction, environment, context) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="praxile-public-instruction-") as directory:
            public = Path(directory) / "instruction.txt"
            public.write_text(instruction, encoding="utf-8")
            await environment.upload_file(public, self.remote_root + "/instruction.txt")
        command = shlex.join([
            self.remote_root + "/venv/bin/python", "-m", "praxile.eval.native_worker",
            "--workspace", self.workspace, "--instruction", self.remote_root + "/instruction.txt",
            "--result", self.remote_root + "/result.json", "--model", self.model_name,
            "--base-url", self.base_url, "--max-steps", str(self.max_steps),
        ])
        await self._exec(environment, command)
        await environment.download_file(self.remote_root + "/result.json", self.logs_dir / "praxile-result.json")
        self.populate_context_post_run(context)

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
