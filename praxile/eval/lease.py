"""Explicit ownership for benchmark workspaces; not a security sandbox."""
from __future__ import annotations

import asyncio
import math
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol, Sequence

from .async_ownership import await_owned
from .artifact_read import DEFAULT_ARTIFACT_BYTES, read_artifact


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class EnvironmentLease(Protocol):
    async def prepare(self) -> None: ...
    async def reset(self) -> None: ...
    async def execute(self, argv: Sequence[str], *, timeout: float) -> ExecutionResult: ...
    async def collect(self, relative_path: str, *, max_bytes: int = DEFAULT_ARTIFACT_BYTES) -> bytes: ...
    async def cancel(self) -> None: ...
    async def cleanup(self) -> None: ...


class LocalWorkspaceLease:
    """Attach to a prepared workspace without adopting ownership of its files.

    The preparer may supply a release callback (e.g. Git worktree cleanup).
    Reset is deliberately unsupported: only the preparer knows the frozen input.
    POSIX process groups are required for descendant cancellation.
    """

    def __init__(self, root: Path, *, release: Callable[[], Awaitable[None]] | None = None):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace must be a directory")
        self._bind_root_identity()
        self._release = release
        self._process: asyncio.subprocess.Process | None = None
        self._state = "new"
        self._busy = False

    def _bind_root_identity(self) -> None:
        info = self.root.stat()
        self._root_identity = (info.st_dev, info.st_ino)

    async def prepare(self) -> None:
        if self._state == "closed":
            raise RuntimeError("lease is closed")
        if os.name != "posix":
            raise NotImplementedError("local leases require POSIX process-group cancellation")
        self._state = "ready"

    async def reset(self) -> None:
        raise NotImplementedError("create a fresh lease through the environment preparer")

    async def execute(self, argv: Sequence[str], *, timeout: float) -> ExecutionResult:
        if self._state != "ready" or self._busy:
            raise RuntimeError("lease is not ready or already executing")
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(a, str) or "\0" in a for a in argv):
            raise ValueError("argv must be a nonempty sequence of strings")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._busy = True
        timed_out = False
        try:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                *argv, cwd=self.root, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            ))
            try:
                self._process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # Cancellation during process creation must not lose ownership.
                self._process = await await_owned(spawning, propagate_cancellation=False)
                await await_owned(self.cancel(), propagate_cancellation=False)
                await await_owned(self._process.communicate(), propagate_cancellation=False)
                raise
            # Shield pipe readers so timeout/cancellation can still drain output.
            reader = asyncio.create_task(self._process.communicate())
            async def stop_and_drain():
                await self.cancel()
                return await reader
            try:
                stdout, stderr = await asyncio.wait_for(asyncio.shield(reader), timeout)
            except TimeoutError:
                timed_out = True
                stdout, stderr = await await_owned(stop_and_drain())
            except asyncio.CancelledError:
                await await_owned(stop_and_drain(), propagate_cancellation=False)
                raise
            return ExecutionResult(self._process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"), timed_out)
        finally:
            try:
                if self._process is not None:
                    await await_owned(self.cancel())
            finally:
                self._process = None
                self._busy = False

    async def cancel(self) -> None:
        if self._process is None:
            return
        # Kill the group even if the parent exited but descendants retain pipes.
        try:
            os.killpg(self._process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await self._process.wait()

    async def collect(self, relative_path: str, *, max_bytes: int = DEFAULT_ARTIFACT_BYTES) -> bytes:
        if self._state != "ready" or self._busy:
            raise RuntimeError("artifact collection requires an idle ready lease")
        return read_artifact(self.root, relative_path, root_identity=self._root_identity, max_bytes=max_bytes)

    async def cleanup(self) -> None:
        if self._state == "closed":
            return
        if self._busy:
            raise RuntimeError("cancel and await execution before cleanup")
        if self._release is not None:
            await self._release()
        self._state = "closed"

    async def __aenter__(self) -> LocalWorkspaceLease:
        await self.prepare()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.cleanup()
