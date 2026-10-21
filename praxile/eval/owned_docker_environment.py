"""Bound command waits in owned Harbor environments, not arbitrary Trials.

This is a per-command limit, not an overall lifecycle deadline. In particular,
upstream uploads and synchronous setup are not covered by the Compose limit.
"""
import math

from harbor.environments.docker.docker import DockerEnvironment

from .lease import LocalWorkspaceLease


class OwnedDockerEnvironment(DockerEnvironment):
    def __init__(self, *args, command_timeout_seconds=120, **kwargs):
        if (type(command_timeout_seconds) not in (int, float)
                or not math.isfinite(command_timeout_seconds) or command_timeout_seconds <= 0):
            raise ValueError("command timeout must be finite and positive")
        self.command_timeout_seconds = command_timeout_seconds
        super().__init__(*args, **kwargs)

    async def _run_docker_compose_command(self, command, check=True, timeout_sec=None,
                                          stdin_data=None, on_output=None):
        # Keep explicit execution budgets intact; bound only omitted deadlines.
        if timeout_sec is None:
            timeout_sec = self.command_timeout_seconds
        return await super()._run_docker_compose_command(
            command, check=check, timeout_sec=timeout_sec, stdin_data=stdin_data,
            on_output=on_output)

    async def _validate_image_os(self, image_name):
        # Upstream inspect has no deadline. Own and reap the local process group.
        async with LocalWorkspaceLease(self.environment_dir) as host:
            result = await host.execute(
                ["docker", "inspect", "--format", "{{.Os}}", image_name],
                timeout=self.command_timeout_seconds)
        if result.timed_out:
            raise RuntimeError("owned image OS inspection timed out")
        if result.returncode:
            # A pinned image may not be local yet; Compose will pull it.
            return
        expected = "windows" if self._is_windows_container else "linux"
        actual = result.stdout.strip().lower()
        if actual != expected:
            raise RuntimeError("owned Docker image OS does not match task OS")
