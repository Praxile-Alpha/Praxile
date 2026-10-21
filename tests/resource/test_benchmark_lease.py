import asyncio
import os
import sys

import pytest

from praxile.eval.lease import LocalWorkspaceLease


pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX lease backend")]


def test_repeated_cancellation_keeps_process_owned_until_reaped(tmp_path, monkeypatch):
    async def exercise():
        lease = LocalWorkspaceLease(tmp_path)
        await lease.prepare()
        original = lease.cancel
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed_cancel():
            started.set()
            await release.wait()
            await original()
        monkeypatch.setattr(lease, "cancel", delayed_cancel)
        pending = asyncio.create_task(lease.execute(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=10))
        process = None
        try:
            async with asyncio.timeout(5):
                while lease._process is None:
                    await asyncio.sleep(.01)
            process = lease._process
            pending.cancel()
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(3):
                pending.cancel()
                await asyncio.sleep(.01)
                assert not pending.done()
                assert lease._busy and lease._process is process
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await lease.cleanup()
        assert process is not None and process.returncode is not None
        assert lease._process is None and lease._busy is False
    asyncio.run(exercise())


def test_execution_collection_and_idempotent_release(tmp_path):
    releases = []

    async def release():
        releases.append(True)

    async def exercise():
        lease = LocalWorkspaceLease(tmp_path, release=release)
        async with lease:
            await lease.prepare()
            result = await lease.execute([sys.executable, "-c", "from pathlib import Path; Path('out.txt').write_text('ok'); print('done')"], timeout=5)
            assert result.returncode == 0
            assert result.stdout == "done\n"
            assert await lease.collect("out.txt") == b"ok"
            with pytest.raises(ValueError):
                await lease.collect("../outside")
        await lease.cleanup()
        assert releases == [True]
        assert (tmp_path / "out.txt").exists()
        with pytest.raises(RuntimeError):
            await lease.prepare()

    asyncio.run(exercise())


def test_timeout_kills_descendant_holding_output_pipe(tmp_path):
    async def exercise():
        async with LocalWorkspaceLease(tmp_path) as lease:
            result = await lease.execute([sys.executable, "-c", "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('spawned',flush=True)"], timeout=0.2)
            assert result.timed_out
            assert "spawned" in result.stdout
            result = await lease.execute([sys.executable, "-c", "print('reusable')"], timeout=5)
            assert result.returncode == 0

    asyncio.run(exercise())


def test_cancellation_and_busy_cleanup(tmp_path):
    async def exercise():
        async with LocalWorkspaceLease(tmp_path) as lease:
            task = asyncio.create_task(lease.execute([sys.executable, "-c", "import time; time.sleep(60)"], timeout=10))
            while lease._process is None:
                await asyncio.sleep(0.01)
            with pytest.raises(RuntimeError):
                await lease.cleanup()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert lease._process is None
            assert not lease._busy

    asyncio.run(exercise())


def test_collect_rejects_external_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret").write_text("private")
    (workspace / "escape").symlink_to(tmp_path / "secret")

    async def exercise():
        async with LocalWorkspaceLease(workspace) as lease:
            with pytest.raises(ValueError):
                await lease.collect("escape")
            with pytest.raises(NotImplementedError):
                await lease.reset()

    asyncio.run(exercise())
