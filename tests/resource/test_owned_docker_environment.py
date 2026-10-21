import asyncio
import sys
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")
from harbor.environments.docker.docker import DockerEnvironment
from praxile.eval.owned_docker_environment import OwnedDockerEnvironment
from praxile.eval.lease import LocalWorkspaceLease, ExecutionResult

pytestmark = pytest.mark.resource


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "120"])
def test_rejects_invalid_deadline_before_upstream_init(value):
    with pytest.raises(ValueError, match="timeout"):
        OwnedDockerEnvironment(command_timeout_seconds=value)


@pytest.mark.parametrize("explicit,expected", [(None, 120), (15, 15), (900, 900)])
def test_compose_default_deadline_preserves_execution_budget(monkeypatch, explicit, expected):
    instance = object.__new__(OwnedDockerEnvironment)
    instance.command_timeout_seconds = 120
    run = AsyncMock(return_value="result")
    monkeypatch.setattr(DockerEnvironment, "_run_docker_compose_command", run)
    callback = object()
    assert asyncio.run(instance._run_docker_compose_command(
        ["up", "--detach", "--wait"], check=False, timeout_sec=explicit,
        stdin_data=b"input", on_output=callback)) == "result"
    assert run.await_args.kwargs == dict(check=False, timeout_sec=expected,
                                       stdin_data=b"input", on_output=callback)


@pytest.mark.parametrize("result,raises", [
    (ExecutionResult(0, "linux\n", ""), False),
    (ExecutionResult(1, "", "not local yet"), False),
    (ExecutionResult(-1, "", "", True), True),
    (ExecutionResult(0, "windows", ""), True),
    (ExecutionResult(0, "", ""), True)])
def test_image_inspect_is_bounded_and_cleanup_owned(tmp_path, monkeypatch, result, raises):
    instance = object.__new__(OwnedDockerEnvironment)
    instance.environment_dir = tmp_path
    instance.command_timeout_seconds = 120
    instance._is_windows_container = False
    execute = AsyncMock(return_value=result)
    cleanup = AsyncMock()
    monkeypatch.setattr(LocalWorkspaceLease, "execute", execute)
    monkeypatch.setattr(LocalWorkspaceLease, "cleanup", cleanup)
    if raises:
        with pytest.raises(RuntimeError):
            asyncio.run(instance._validate_image_os("pinned-image"))
    else:
        asyncio.run(instance._validate_image_os("pinned-image"))
    assert execute.await_args.kwargs["timeout"] == 120
    cleanup.assert_awaited_once()


def test_stalled_compose_process_is_reaped_on_deadline(tmp_path, monkeypatch):
    instance = object.__new__(OwnedDockerEnvironment)
    instance.command_timeout_seconds = .05
    instance.environment_dir = tmp_path
    instance.environment_name = instance.session_id = "praxile-lease-fixture"
    monkeypatch.setattr(OwnedDockerEnvironment, "_docker_compose_paths", property(lambda self: []))
    monkeypatch.setattr(instance, "_compose_env_vars", lambda **kwargs: None)
    spawn = asyncio.create_subprocess_exec
    children = []

    async def substitute(*args, **kwargs):
        child = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", substitute)

    async def exercise():
        try:
            with pytest.raises(RuntimeError, match="timed out"):
                await instance._run_docker_compose_command(["up", "--detach", "--wait"])
            assert len(children) == 1 and children[0].returncode is not None
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.communicate()
    asyncio.run(exercise())
