import asyncio
import os
import subprocess
import sys
import threading

import pytest

from praxile.eval.v2.schema import EvalTask, RepositorySpec, SWEbenchEvaluationSpec
from praxile.eval.worktree_lease import WorktreeLease
from praxile.eval.v2.repository import BenchmarkRepositoryPreparer

pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")]


def source_task(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=source, check=True,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (source / "input.txt").write_text("frozen")
    git("add", ".")
    git("commit", "-qm", "base")
    task = EvalTask(task_id="owner__repo-1", instruction="test",
                   repository=RepositorySpec("owner/repo", git("rev-parse", "HEAD"),
                                             "https://invalid.example/repo.git"),
                   evaluation=SWEbenchEvaluationSpec("fixture", "test"))
    return source, task


def test_reset_is_frozen_and_other_leases_and_source_are_untouched(tmp_path):
    source, task = source_task(tmp_path)
    async def exercise():
        first = await WorktreeLease.create(tmp_path / "state", task, source_override=source)
        second = await WorktreeLease.create(tmp_path / "state", task, source_override=source)
        try:
            assert first.root != second.root
            result = await first.execute([sys.executable, "-c",
                "from pathlib import Path; Path('input.txt').write_text('changed'); Path('extra').touch()"], timeout=5)
            assert result.returncode == 0
            assert await first.collect("input.txt") == b"changed"
            await first.reset()
            assert await first.collect("input.txt") == b"frozen"
            assert not (first.root / "extra").exists()
            assert await second.collect("input.txt") == b"frozen"
            assert (source / "input.txt").read_text() == "frozen"
            assert first.prepared.resolved_commit == task.repository.base_commit
        finally:
            await first.cleanup()
            await second.cleanup()
        await first.cleanup()
        assert not first.root.exists()
        assert source.exists()
        with pytest.raises(RuntimeError):
            await first.reset()
    asyncio.run(exercise())


def test_cancelled_creation_releases_the_completed_worktree(tmp_path, monkeypatch):
    source, task = source_task(tmp_path)
    original = BenchmarkRepositoryPreparer.prepare
    started, proceed = threading.Event(), threading.Event()
    prepared = []
    def delayed(self, *args, **kwargs):
        started.set()
        if not proceed.wait(5):
            raise RuntimeError("test synchronization timed out")
        result = original(self, *args, **kwargs)
        prepared.append(result)
        return result
    monkeypatch.setattr(BenchmarkRepositoryPreparer, "prepare", delayed)
    async def exercise():
        pending = asyncio.create_task(WorktreeLease.create(
            tmp_path / "state", task, source_override=source))
        try:
            async with asyncio.timeout(5):
                while not started.is_set():
                    await asyncio.sleep(.01)
            pending.cancel()
        finally:
            proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(prepared) == 1
        assert not prepared[0].workspace_root.exists()
        assert (source / "input.txt").read_text() == "frozen"
    asyncio.run(exercise())


def test_failed_reset_blocks_execution_but_allows_cleanup(tmp_path, monkeypatch):
    source, task = source_task(tmp_path)
    async def exercise():
        lease = await WorktreeLease.create(tmp_path / "state", task, source_override=source)
        def fail(*args, **kwargs):
            raise OSError("prepare failed")
        monkeypatch.setattr(lease._preparer, "prepare", fail)
        try:
            with pytest.raises(OSError, match="prepare failed"):
                await lease.reset()
            with pytest.raises(RuntimeError):
                await lease.execute([sys.executable, "-c", "pass"], timeout=5)
            with pytest.raises(RuntimeError):
                await lease.prepare()
        finally:
            await lease.cleanup()
        assert not lease.root.exists()
    asyncio.run(exercise())


def test_active_execution_rejects_reset_then_can_be_cancelled(tmp_path):
    source, task = source_task(tmp_path)
    async def exercise():
        lease = await WorktreeLease.create(tmp_path / "state", task, source_override=source)
        async with lease:
            running = asyncio.create_task(lease.execute(
                [sys.executable, "-c", "import time; time.sleep(60)"], timeout=10))
            try:
                async with asyncio.timeout(5):
                    while lease._process is None:
                        await asyncio.sleep(.01)
                with pytest.raises(RuntimeError):
                    await lease.reset()
            finally:
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            await lease.reset()
            assert await lease.collect("input.txt") == b"frozen"
    asyncio.run(exercise())
