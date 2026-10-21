import asyncio
import hashlib
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")
from praxile.eval.harbor_lease import HarborDockerLease
from praxile.eval.contracts import InputArtifact

pytestmark = [pytest.mark.resource, pytest.mark.integration]
IMAGE = "python@sha256:" + "a" * 64


def test_pinned_image_required(tmp_path):
    with pytest.raises(ValueError, match="digest-pinned"):
        HarborDockerLease(tmp_path, image="python:latest")


def test_public_input_view_does_not_expose_host_references(tmp_path):
    source = tmp_path / "private-source-location"
    source.mkdir()
    (source / "original-name").write_bytes(b"public")
    digest = "sha256:" + hashlib.sha256(b"public").hexdigest()
    lease = HarborDockerLease(tmp_path, image=IMAGE,
        inputs=(InputArtifact("task.txt", "original-name", digest),), input_source_root=source)
    expected = [{"path": "inputs/task.txt", "content_digest": digest}]
    assert lease.public_inputs == expected
    view = lease.public_inputs
    view[0]["path"] = "mutated"
    view.clear()
    assert lease.public_inputs == expected


def test_adapter_owns_exclusive_execution_and_cancellation(tmp_path, monkeypatch):
    async def exercise():
        lease = HarborDockerLease(tmp_path, image=IMAGE)
        lease._state = "ready"
        lease._environment = object()
        started = asyncio.Event()
        async def setup(environment):
            assert environment is lease._environment
            started.set()
            await asyncio.Event().wait()
        agent = SimpleNamespace(setup=setup, run=AsyncMock())
        destroyed = AsyncMock()
        monkeypatch.setattr(lease, "_destroy", destroyed)
        pending = asyncio.create_task(lease.run_adapter(agent, "task", object()))
        try:
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(RuntimeError):
                await lease.execute(["true"], timeout=1)
            with pytest.raises(RuntimeError):
                await lease.reset()
            with pytest.raises(RuntimeError):
                await lease.cleanup()
        finally:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        destroyed.assert_awaited_once()
        agent.run.assert_not_awaited()
        assert lease._state == "failed" and not lease._busy
    asyncio.run(exercise())


def test_cleanup_verifies_resources_and_can_retry(tmp_path, monkeypatch):
    async def exercise():
        lease = HarborDockerLease(tmp_path, image=IMAGE)
        environment = SimpleNamespace(stop=AsyncMock())
        lease._environment, lease._session = environment, "praxile-lease-fixture"
        lease._state = "ready"
        query = AsyncMock(return_value="still-present")
        monkeypatch.setattr(lease, "_docker", query)
        with pytest.raises(RuntimeError, match="left owned container"):
            await lease.cleanup()
        assert lease._state == "failed"
        assert lease._environment is environment
        with pytest.raises(RuntimeError):
            await lease.prepare()
        query.return_value = ""
        await lease.cleanup()
        await lease.cleanup()
        assert lease._state == "closed"
        assert environment.stop.await_count == 2
    asyncio.run(exercise())


def test_cleanup_refuses_changed_daemon(tmp_path, monkeypatch):
    async def exercise():
        lease = HarborDockerLease(tmp_path, image=IMAGE)
        environment = SimpleNamespace(stop=AsyncMock())
        lease._environment, lease._session = environment, "praxile-lease-" + "a" * 32
        lease._state = "ready"
        lease._generations = [{"session": lease._session, "daemon_id": "original", "phase": "ready"}]
        monkeypatch.setattr(lease, "_docker", AsyncMock(return_value="different"))
        with pytest.raises(RuntimeError, match="different daemon"):
            await lease.cleanup()
        environment.stop.assert_not_awaited()
        assert lease._environment is environment and lease._state == "failed"
    asyncio.run(exercise())


def test_cleanup_repeated_cancellation_keeps_ownership(tmp_path, monkeypatch):
    async def exercise():
        lease = HarborDockerLease(tmp_path, image=IMAGE)
        started, finish = asyncio.Event(), asyncio.Event()
        async def stop(**kwargs):
            started.set()
            await finish.wait()
        lease._environment = SimpleNamespace(stop=AsyncMock(side_effect=stop))
        lease._session, lease._state = "praxile-lease-fixture", "ready"
        monkeypatch.setattr(lease, "_docker", AsyncMock(return_value=""))
        operation = asyncio.create_task(lease.cleanup())
        try:
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(3):
                operation.cancel()
                await asyncio.sleep(.01)
                assert not operation.done()
            with pytest.raises(RuntimeError):
                await lease.execute(["true"], timeout=1)
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(operation, 5)
        assert lease._state == "closed"
    asyncio.run(exercise())


@pytest.mark.parametrize("method", ["prepare", "reset"])
def test_cancelled_preparation_drains_then_destroys(tmp_path, monkeypatch, method):
    async def exercise():
        lease = HarborDockerLease(tmp_path, image=IMAGE)
        started, finish = asyncio.Event(), asyncio.Event()
        async def prepare():
            started.set()
            await finish.wait()
            lease._state = "ready"
        destroy = AsyncMock()
        monkeypatch.setattr(lease, "_prepare_owned", prepare)
        monkeypatch.setattr(lease, "_destroy", destroy)
        pending = asyncio.create_task(getattr(lease, method)())
        try:
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(3):
                pending.cancel()
                await asyncio.sleep(.01)
                assert not pending.done()
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
        assert lease._state == "failed"
        assert destroy.await_count == (2 if method == "reset" else 1)
    asyncio.run(exercise())


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_LEASE_IMAGE"), reason="requires pinned local Docker image")
def test_real_owned_docker_lifecycle(tmp_path):
    async def exercise():
        source = tmp_path / "source"
        source.mkdir()
        (source / "public.txt").write_bytes(b"frozen task")
        (source / "rubric.txt").write_text("private judge only")
        inputs = (InputArtifact("task.txt", "public.txt",
            "sha256:" + hashlib.sha256(b"frozen task").hexdigest()),)
        lease = HarborDockerLease(tmp_path, image=os.environ["PRAXILE_TEST_LEASE_IMAGE"],
                                  inputs=inputs, input_source_root=source)
        try:
            await lease.prepare()
            first_session = lease._session
            from praxile.eval.lease_inspect import inspect_lease
            observed = await inspect_lease(lease.root)
            assert observed["resources_absent"] is False
            assert observed["generations"][0]["observation"] == "present"
            result = await lease.execute(["python3", "-c",
                "from pathlib import Path; p=Path('/workspace/inputs'); "
                "assert sorted(x.name for x in p.iterdir()) == ['task.txt']; "
                "assert (p/'task.txt').read_bytes() == b'frozen task'"], timeout=10)
            assert result.returncode == 0
            assert lease.public_inputs == [{"path": "inputs/task.txt", "content_digest": inputs[0].content_digest}]
            assert await lease.collect("inputs/task.txt") == b"frozen task"
            for command in (["touch", "/workspace/inputs/new"], ["rm", "/workspace/inputs/task.txt"],
                            ["sh", "-c", "echo changed > /workspace/inputs/task.txt"]):
                assert (await lease.execute(command, timeout=10)).returncode != 0
            result = await lease.execute(["python3", "-c",
                "from pathlib import Path; Path('artifact').write_bytes(b'hello')"], timeout=10)
            assert result.returncode == 0
            assert await lease.collect("artifact") == b"hello"
            with pytest.raises(ValueError):
                await lease.collect("artifact", max_bytes=2)
            await lease.execute(["ln", "-s", "/etc/passwd", "escape"], timeout=10)
            with pytest.raises(ValueError):
                await lease.collect("escape")
            (source / "public.txt").write_text("source changed")
            await lease.reset()
            assert lease._session != first_session
            assert (await lease.execute(["cat", "inputs/task.txt"], timeout=10)).stdout == "frozen task"
            with pytest.raises(ValueError):
                await lease.collect("artifact")
            result = await lease.execute(["python3", "-c", "import time; time.sleep(60)"], timeout=.2)
            assert result.timed_out
            assert lease._state == "failed"
            assert lease._environment is None
            await lease.reset()
            pending = asyncio.create_task(lease.execute(
                ["python3", "-c", "import time; time.sleep(60)"], timeout=10))
            await asyncio.sleep(.2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert lease._environment is None
        finally:
            await lease.cleanup()
        assert lease._state == "closed"
        observed = await inspect_lease(lease.root)
        assert observed["resources_absent"] is True
        assert len(observed["generations"]) == 3
    asyncio.run(exercise())


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_LEASE_IMAGE"), reason="requires pinned local Docker image")
def test_native_file_tools_can_read_but_not_mutate_mounted_inputs(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "public.txt").write_text("native tool input")
    inputs = (InputArtifact("task.txt", "public.txt",
        "sha256:" + hashlib.sha256(b"native tool input").hexdigest()),)
    # Test the current source, not an unrelated previously built wheel.
    package = Path(__file__).resolve().parents[2] / "praxile"
    bundle = tmp_path / "native-source.tar"
    with tarfile.open(bundle, "w") as archive:
        for path in sorted(package.rglob("*.py")):
            if "__pycache__" not in path.parts:
                archive.add(path, arcname="praxile/" + path.relative_to(package).as_posix())

    async def exercise():
        async with HarborDockerLease(tmp_path, image=os.environ["PRAXILE_TEST_LEASE_IMAGE"],
                                     inputs=inputs, input_source_root=source) as lease:
            await lease._environment.upload_file(bundle, "/tmp/native-source.tar")
            script = '''
import errno, sys, tarfile
from pathlib import Path
with tarfile.open('/tmp/native-source.tar') as archive:
    archive.extractall('/tmp/native-source', filter='data')
sys.path.insert(0, '/tmp/native-source')
from praxile.config import Config
from praxile.security import SafetyPolicy
from praxile.environment import FileSystemEnv
config = Config.load(Path('/workspace'))
fs = FileSystemEnv(config, SafetyPolicy(config))
result = fs.read_file('inputs/task.txt')
assert result.status == 'success' and 'native tool input' in result.output
assert 'inputs/task.txt' in fs.list_dir('inputs').output
assert fs.read_file('/etc/passwd').status == 'blocked'
for path in ('inputs/task.txt', 'inputs/new.txt'):
    try:
        fs.write_file(path, 'changed', task_id='fixture', step=1)
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        raise AssertionError('native tool changed a read-only input')
assert Path('/workspace/inputs/task.txt').read_text() == 'native tool input'
assert not Path('/workspace/inputs/new.txt').exists()
print('native file tools verified')
'''
            result = await lease.execute(["python3", "-I", "-c", script], timeout=30)
            assert result.returncode == 0, result.stderr
            assert "native file tools verified" in result.stdout
    asyncio.run(exercise())
