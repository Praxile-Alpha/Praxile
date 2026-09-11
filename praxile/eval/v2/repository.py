from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...utils import file_lock, run_process, utc_now
from .schema import EvalSchemaError, EvalTask


_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}")


@dataclass(frozen=True)
class PreparedRepository:
    task_id: str
    source: str
    requested_commit: str
    resolved_commit: str
    workspace_root: Path
    mirror_root: Path
    created_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "source": self.source,
            "requested_commit": self.requested_commit,
            "resolved_commit": self.resolved_commit,
            "workspace_root": str(self.workspace_root),
            "mirror_root": str(self.mirror_root),
            "created_at": self.created_at,
        }


class BenchmarkRepositoryPreparer:
    """Prepare benchmark commits as detached worktrees outside the source project."""

    def __init__(self, state_root: Path):
        self.root = state_root.resolve() / "eval" / "v2"
        self.mirrors_root = self.root / "repositories"
        self.runs_root = self.root / "runs"

    def prepare(self, task: EvalTask, *, eval_run_id: str, source_override: Path | None = None) -> PreparedRepository:
        source = str(source_override.resolve()) if source_override else task.repository.clone_url
        mirror = self.mirrors_root / hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
        workspace = self.runs_root / _safe(eval_run_id) / "workspaces" / _safe(task.task_id)
        lock_target = self.mirrors_root / f"{mirror.name}.operation"
        self.mirrors_root.mkdir(parents=True, exist_ok=True)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(lock_target, timeout_seconds=120):
            self._ensure_mirror(source, mirror)
            resolved = self._resolve_commit(mirror, task.repository.base_commit, allow_fetch=source_override is None)
            if workspace.exists():
                self._remove_worktree(mirror, workspace)
            result = run_process(
                ["git", f"--git-dir={mirror}", "worktree", "add", "--detach", str(workspace), resolved],
                cwd=self.root,
                timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(f"failed to prepare worktree for {task.task_id}: {result.stderr.strip()}")
        actual = run_process(["git", "rev-parse", "HEAD"], cwd=workspace, timeout=30)
        status = run_process(["git", "status", "--porcelain", "--untracked-files=no"], cwd=workspace, timeout=30)
        if actual.returncode != 0 or actual.stdout.strip() != resolved or status.returncode != 0 or status.stdout.strip():
            self.cleanup(
                PreparedRepository(task.task_id, source, task.repository.base_commit, resolved, workspace, mirror, utc_now())
            )
            raise RuntimeError(f"prepared repository validation failed for {task.task_id}")
        return PreparedRepository(
            task_id=task.task_id,
            source=source,
            requested_commit=task.repository.base_commit,
            resolved_commit=resolved,
            workspace_root=workspace.resolve(),
            mirror_root=mirror.resolve(),
            created_at=utc_now(),
        )

    def cleanup(self, prepared: PreparedRepository) -> None:
        if not prepared.workspace_root.exists():
            return
        with file_lock(self.mirrors_root / f"{prepared.mirror_root.name}.operation", timeout_seconds=120):
            self._remove_worktree(prepared.mirror_root, prepared.workspace_root)

    def _ensure_mirror(self, source: str, mirror: Path) -> None:
        if mirror.exists():
            check = run_process(["git", f"--git-dir={mirror}", "rev-parse", "--is-bare-repository"], cwd=self.root, timeout=30)
            if check.returncode != 0 or check.stdout.strip() != "true":
                raise RuntimeError(f"benchmark mirror is not a bare Git repository: {mirror}")
            return
        result = run_process(["git", "clone", "--mirror", "--no-hardlinks", source, str(mirror)], cwd=self.root, timeout=300)
        if result.returncode != 0:
            shutil.rmtree(mirror, ignore_errors=True)
            raise RuntimeError(f"failed to clone benchmark repository {source}: {result.stderr.strip()}")

    def _resolve_commit(self, mirror: Path, revision: str, *, allow_fetch: bool) -> str:
        result = run_process(["git", f"--git-dir={mirror}", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=self.root, timeout=30)
        if result.returncode != 0 and allow_fetch:
            fetched = run_process(
                ["git", f"--git-dir={mirror}", "fetch", "--no-tags", "origin", revision], cwd=self.root, timeout=300
            )
            if fetched.returncode == 0:
                result = run_process(
                    ["git", f"--git-dir={mirror}", "rev-parse", "--verify", f"{revision}^{{commit}}"],
                    cwd=self.root,
                    timeout=30,
                )
        if result.returncode != 0:
            raise EvalSchemaError(f"base commit is unavailable: {revision}")
        resolved = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", resolved):
            raise RuntimeError(f"Git returned an invalid resolved commit: {resolved!r}")
        return resolved.lower()

    @staticmethod
    def _remove_worktree(mirror: Path, workspace: Path) -> None:
        result = run_process(
            ["git", f"--git-dir={mirror}", "worktree", "remove", "--force", str(workspace)],
            cwd=mirror.parent,
            timeout=120,
        )
        if result.returncode != 0 and workspace.exists():
            shutil.rmtree(workspace)
        run_process(["git", f"--git-dir={mirror}", "worktree", "prune"], cwd=mirror.parent, timeout=30)


def _safe(value: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise EvalSchemaError(f"unsafe local eval identifier: {value!r}")
    return value
