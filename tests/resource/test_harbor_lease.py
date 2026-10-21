import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")
from praxile.eval.harbor_lease import HarborDockerLease

pytestmark = [pytest.mark.resource, pytest.mark.integration]
IMAGE = "python@sha256:" + "a" * 64


def test_pinned_image_required(tmp_path):
    with pytest.raises(ValueError, match="digest-pinned"):
        HarborDockerLease(tmp_path, image="python:latest")


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
        lease = HarborDockerLease(tmp_path, image=os.environ["PRAXILE_TEST_LEASE_IMAGE"])
        try:
            await lease.prepare()
            first_session = lease._session
            result = await lease.execute(["python3", "-c",
                "from pathlib import Path; Path('artifact').write_bytes(b'hello')"], timeout=10)
            assert result.returncode == 0
            assert await lease.collect("artifact") == b"hello"
            with pytest.raises(ValueError):
                await lease.collect("artifact", max_bytes=2)
            await lease.execute(["ln", "-s", "/etc/passwd", "escape"], timeout=10)
            with pytest.raises(ValueError):
                await lease.collect("escape")
            await lease.reset()
            assert lease._session != first_session
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
    asyncio.run(exercise())
