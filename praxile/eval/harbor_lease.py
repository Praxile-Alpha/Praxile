"""Opt-in, owned Harbor Docker leases; never adopt a Trial environment.

The image must contain Python 3. Cancellation destroys the environment, so a
reset is required before further work. This is not a hostile-code sandbox.
"""
from __future__ import annotations

import asyncio
import base64
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import shlex
import uuid

from . import artifact_read
from .async_ownership import await_owned
from .lease import ExecutionResult, LocalWorkspaceLease
from .contracts import InputArtifact
from .input_snapshot import freeze_public_inputs, verify_public_inputs


class HarborDockerLease:
    def __init__(self, state_root: Path, *, image: str,
                 inputs: tuple[InputArtifact, ...] = (), input_source_root: Path | None = None):
        if not re.fullmatch(r"[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}", image):
            raise ValueError("a digest-pinned Docker image is required")
        if version("harbor") not in {"0.18.0", "0.22.0"}:
            raise ValueError("owned leases support Harbor 0.18.0 or 0.22.0")
        if not isinstance(inputs, tuple) or bool(inputs) != (input_source_root is not None):
            raise ValueError("nonempty public inputs and source root must be supplied together")
        self.image = image
        self.root = state_root.resolve() / ("harbor-lease-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._state = "new"
        self._busy = False
        self._lifecycle = asyncio.Lock()
        self._environment = None
        self._session = None
        self._root_identity = None
        self._inputs = inputs
        self._input_identity = None
        if inputs:
            self._input_identity = freeze_public_inputs(input_source_root, inputs, self.root / "inputs")

    async def _docker(self, *argv):
        async with LocalWorkspaceLease(self.root) as host:
            result = await host.execute(["docker", *argv], timeout=30)
        if result.timed_out or result.returncode:
            raise RuntimeError("Docker ownership query/cleanup failed")
        return result.stdout.strip()

    async def _destroy(self):
        if self._environment is None:
            return
        # Harbor stop may swallow errors; independently inspect owned labels.
        await self._environment.stop(delete=True)
        label = "label=com.docker.compose.project=" + self._session
        for resource in ("container", "network", "volume"):
            remaining = await self._docker(resource, "ls", "--filter", label, "-q",
                                           *(["-a"] if resource == "container" else []))
            if remaining:
                raise RuntimeError("Harbor cleanup left owned " + resource + " resources")
        self._environment = None

    async def prepare(self):
        async with self._lifecycle:
            if self._state == "ready":
                return
            if self._state != "new":
                raise RuntimeError("lease requires reset or cleanup")
            try:
                await await_owned(self._prepare_owned())
            except asyncio.CancelledError:
                self._state = "failed"
                await await_owned(self._destroy(), propagate_cancellation=False)
                raise

    async def _prepare_owned(self):
        from harbor.environments.docker.docker import DockerEnvironment
        from harbor.models.task.config import EnvironmentConfig
        from harbor.models.trial.paths import TrialPaths

        self._state = "preparing"
        mounts = []
        if self._inputs:
            try:
                verify_public_inputs(self.root / "inputs", self._inputs, self._input_identity)
            except BaseException:
                self._state = "failed"
                raise
            mounts = [{"type": "bind", "source": str(self.root / "inputs"),
                       "target": "/inputs", "read_only": True}]
        self._session = "praxile-lease-" + uuid.uuid4().hex
        generation = self.root / self._session
        environment_dir = generation / "environment"
        environment_dir.mkdir(parents=True)
        paths = TrialPaths(generation / "trial")
        paths.mkdir()
        self._environment = DockerEnvironment(
            environment_dir=environment_dir, environment_name=self._session,
            session_id=self._session, trial_paths=paths, keep_containers=False,
            task_env_config=EnvironmentConfig(docker_image=self.image, workdir="/workspace"),
            mounts=mounts,
        )
        try:
            await self._environment.start(force_build=False)
            result = await self._environment.exec(shlex.join([
                "python3", "-I", "-c",
                "import os,json; os.makedirs('/workspace',exist_ok=True); "
                "s=os.stat('/workspace'); print(json.dumps([s.st_dev,s.st_ino]))",
            ]), cwd="/", timeout_sec=15)
            if result.return_code:
                raise RuntimeError("container Python/workspace preflight failed")
            self._root_identity = tuple(json.loads(result.stdout))
            self._state = "ready"
        except BaseException:
            self._state = "failed"
            await self._destroy()
            raise

    async def reset(self):
        async with self._lifecycle:
            if self._busy or self._state == "closed":
                raise RuntimeError("reset requires an idle nonclosed lease")
            async def replace():
                self._state = "resetting"
                try:
                    await self._destroy()
                    await self._prepare_owned()
                except BaseException:
                    self._state = "failed"
                    raise
            try:
                await await_owned(replace())
            except asyncio.CancelledError:
                self._state = "failed"
                await await_owned(self._destroy(), propagate_cancellation=False)
                raise

    async def execute(self, argv, *, timeout):
        if (isinstance(argv, (str, bytes)) or not argv
                or any(not isinstance(a, str) or "\0" in a for a in argv)):
            raise ValueError("argv must be a nonempty sequence of strings")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if self._state != "ready" or self._busy:
            raise RuntimeError("execution requires an idle ready lease")
        self._busy = True
        operation = asyncio.create_task(self._environment.exec(
            shlex.join(argv), cwd="/workspace", timeout_sec=math.ceil(timeout)))
        try:
            result = await asyncio.wait_for(asyncio.shield(operation), timeout)
            return ExecutionResult(result.return_code, result.stdout or "", result.stderr or "")
        except (TimeoutError, asyncio.CancelledError) as error:
            async def stop_and_drain():
                try:
                    await self.cancel()
                finally:
                    # The Harbor exec owns its subprocess; never abandon its wait.
                    try:
                        await operation
                    except Exception:
                        pass
            await await_owned(stop_and_drain(), propagate_cancellation=False)
            if isinstance(error, asyncio.CancelledError):
                raise
            return ExecutionResult(-1, "", "execution timed out; reset required", True)
        except Exception:
            await await_owned(self.cancel())
            raise
        finally:
            self._busy = False

    async def collect(self, relative_path, *, max_bytes=artifact_read.DEFAULT_ARTIFACT_BYTES):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 256 * 1024**2:
            raise ValueError("artifact read limit must be 1..256 MiB")
        if not isinstance(relative_path, str):
            raise ValueError("artifact path must be a string")
        # Transfer only bounded bytes, never an unchecked recursive docker cp.
        source = Path(artifact_read.__file__).read_text(encoding="utf-8")
        script = source + "\nimport base64,json,sys\na=json.loads(sys.argv[1])\n" + (
            "print(base64.b64encode(read_artifact('/workspace', a['path'], "
            "root_identity=tuple(a['identity']), max_bytes=a['limit'])).decode('ascii'))\n")
        result = await self.execute(["python3", "-I", "-c", script, json.dumps({
            "path": relative_path, "identity": self._root_identity, "limit": max_bytes,
        })], timeout=30)
        if result.returncode or result.timed_out:
            raise ValueError("artifact collection failed")
        if len(result.stdout) > ((max_bytes + 2) // 3) * 4 + 1:
            raise ValueError("artifact transfer exceeded byte limit")
        content = base64.b64decode(result.stdout.strip(), validate=True)
        if len(content) > max_bytes:
            raise ValueError("artifact transfer exceeded byte limit")
        return content

    async def cancel(self):
        async with self._lifecycle:
            if self._state == "closed":
                return
            self._state = "failed"
            await await_owned(self._destroy())

    async def cleanup(self):
        async with self._lifecycle:
            if self._state == "closed":
                return
            if self._busy:
                raise RuntimeError("cancel and await execution before cleanup")
            self._state = "closing"
            async def close():
                try:
                    await self._destroy()
                except BaseException:
                    self._state = "failed"
                    raise
                self._state = "closed"
            await await_owned(close())

    async def __aenter__(self):
        try:
            await self.prepare()
        except BaseException:
            await await_owned(self.cleanup())
            raise
        return self

    async def __aexit__(self, *exc):
        await self.cleanup()
