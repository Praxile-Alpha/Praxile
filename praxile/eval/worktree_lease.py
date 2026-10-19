"""Owned Git worktree leases using the existing benchmark repository preparer."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from .lease import LocalWorkspaceLease
from .v2.repository import BenchmarkRepositoryPreparer, PreparedRepository
from .v2.schema import EvalTask
from ..utils import new_id


class WorktreeLease(LocalWorkspaceLease):
    """Create through ``create``; never adopt an arbitrary user directory.

    Reset discards changes only in this lease's generated worktree. This is
    filesystem separation, not a sandbox for commands or Git metadata access.
    """

    def __init__(self, prepared: PreparedRepository, preparer: BenchmarkRepositoryPreparer,
                 task: EvalTask, run_id: str, source_override: Path | None):
        super().__init__(prepared.workspace_root)
        self.prepared = prepared
        self._preparer = preparer
        self._task = replace(task, repository=replace(
            task.repository, base_commit=prepared.resolved_commit))
        self._run_id = run_id
        self._source_override = source_override
        self._lifecycle = asyncio.Lock()

    @classmethod
    async def create(cls, state_root: Path, task: EvalTask, *,
                     source_override: Path | None = None) -> WorktreeLease:
        preparer = BenchmarkRepositoryPreparer(state_root)
        run_id = new_id("lease")
        operation = asyncio.create_task(asyncio.to_thread(
            preparer.prepare, task, eval_run_id=run_id, source_override=source_override))
        try:
            prepared = await asyncio.shield(operation)
        except asyncio.CancelledError:
            # A cancelled await does not stop the preparer's worker thread.
            prepared = await operation
            await asyncio.to_thread(preparer.cleanup, prepared)
            raise
        lease = cls(prepared, preparer, task, run_id, source_override)
        try:
            await lease.prepare()
        except BaseException:
            await asyncio.to_thread(preparer.cleanup, prepared)
            raise
        return lease

    async def prepare(self) -> None:
        async with self._lifecycle:
            if self._state not in {"new", "ready"}:
                raise RuntimeError("lease cannot be prepared in its current state")
            await super().prepare()

    async def reset(self) -> None:
        async with self._lifecycle:
            if self._state != "ready" or self._busy:
                raise RuntimeError("reset requires an idle ready lease")
            self._state = "resetting"
            operation = asyncio.create_task(asyncio.to_thread(
                self._preparer.prepare, self._task, eval_run_id=self._run_id,
                source_override=self._source_override))
            try:
                self.prepared = await asyncio.shield(operation)
            except asyncio.CancelledError:
                try:
                    self.prepared = await operation
                finally:
                    self._state = "failed"
                raise
            except BaseException:
                self._state = "failed"
                raise
            self.root = self.prepared.workspace_root
            self._state = "ready"

    async def cleanup(self) -> None:
        async with self._lifecycle:
            if self._state == "closed":
                return
            if self._busy:
                raise RuntimeError("cancel and await execution before cleanup")
            self._state = "closing"
            operation = asyncio.create_task(asyncio.to_thread(self._preparer.cleanup, self.prepared))
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                await operation
                self._state = "closed"
                raise
            self._state = "closed"
