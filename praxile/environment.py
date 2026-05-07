from __future__ import annotations

import os
import signal
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .constants import DEFAULT_EXCLUDES
from .inspector import inspect_project
from .project_map import build_project_map, format_project_map
from .search_backend import make_search_backend
from .security import SafetyPolicy
from .utils import run_process, shorten, stable_hash, unified_diff, utc_now


def _has_shell_features(command: str) -> bool:
    return any(token in command for token in ["&&", "||", ";", "|", ">", "<", "$(", "`", "$"])


@dataclass
class Observation:
    status: str
    output: str = ""
    data: dict[str, Any] | None = None
    risk_level: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output,
            "data": self.data or {},
            "risk_level": self.risk_level,
        }


class FileSystemEnv:
    def __init__(self, config: Config, safety: SafetyPolicy):
        self.config = config
        self.root = config.paths.root
        self.paths = config.paths
        self.safety = safety

    def list_files(self, *, limit: int = 200) -> Observation:
        project_map = build_project_map(self.config, max_files=limit)
        project_map["skipped_protected"] = project_map.get("protected_skipped", 0)
        return Observation(
            "success",
            format_project_map(project_map),
            project_map,
        )

    def list_dir(self, path: str = ".", *, max_files: int = 100) -> Observation:
        decision = self.safety.check_path(path)
        if path not in {"", "."} and not decision.allowed:
            return Observation("blocked", decision.reason, risk_level=decision.risk_level)
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root.resolve())
        except ValueError:
            return Observation("blocked", f"path escapes project root: {path}", risk_level="high")
        if not target.exists():
            return Observation("failure", f"directory not found: {path}")
        if not target.is_dir():
            return Observation("failure", f"not a directory: {path}")
        entries: list[str] = []
        skipped_protected = 0
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            rel = child.relative_to(self.root).as_posix()
            if any(part in DEFAULT_EXCLUDES for part in Path(rel).parts):
                continue
            child_decision = self.safety.check_path(rel)
            if not child_decision.allowed:
                skipped_protected += 1
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(rel + suffix)
            if len(entries) >= max_files:
                break
        return Observation(
            "success",
            "\n".join(entries),
            {
                "path": path or ".",
                "count": len(entries),
                "truncated": len(entries) >= max_files,
                "skipped_protected": skipped_protected,
            },
        )

    def find_files(self, query: str, *, limit: int = 80) -> Observation:
        lowered = query.lower().strip()
        if not lowered:
            return Observation("failure", "file query is empty")
        matches: list[str] = []
        skipped_protected = 0
        for current, dirs, filenames in os.walk(self.root):
            current_path = Path(current)
            rel_parts = set(current_path.relative_to(self.root).parts) if current_path != self.root else set()
            if rel_parts & DEFAULT_EXCLUDES:
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name not in DEFAULT_EXCLUDES]
            for filename in filenames:
                rel = (current_path / filename).relative_to(self.root).as_posix()
                decision = self.safety.check_path(rel)
                if not decision.allowed:
                    skipped_protected += 1
                    continue
                if lowered in rel.lower():
                    matches.append(rel)
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        return Observation(
            "success",
            "\n".join(matches),
            {"query": query, "count": len(matches), "truncated": len(matches) >= limit, "skipped_protected": skipped_protected},
        )

    def project_map(self, *, refresh: bool = False) -> Observation:
        data = build_project_map(self.config, refresh=refresh)
        return Observation("success", format_project_map(data), data)

    def read_file(
        self,
        path: str,
        *,
        max_chars: int = 20000,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> Observation:
        decision = self.safety.check_path(path)
        if not decision.allowed:
            return Observation("blocked", decision.reason, risk_level=decision.risk_level)
        target = (self.root / path).resolve()
        if not target.exists():
            return Observation("failure", f"file not found: {path}")
        if not target.is_file():
            return Observation("failure", f"not a file: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines)
        requested_start = max(1, int(start_line or 1))
        requested_end = int(end_line or total_lines or requested_start)
        if start_line is not None or end_line is not None:
            if requested_start > max(total_lines, 1):
                return Observation(
                    "failure",
                    f"start_line {requested_start} is beyond file length {total_lines}",
                    {"path": path, "total_lines": total_lines},
                )
            requested_end = max(requested_start, min(requested_end, total_lines))
            selected = lines[requested_start - 1 : requested_end]
            text = "\n".join(f"{line_no}: {line}" for line_no, line in enumerate(selected, requested_start))
        output = shorten(text, max_chars)
        return Observation(
            "success",
            output,
            {
                "path": path,
                "truncated": len(text) > max_chars,
                "total_lines": total_lines,
                "start_line": requested_start if start_line is not None or end_line is not None else None,
                "end_line": requested_end if start_line is not None or end_line is not None else None,
                "has_more_before": bool((start_line is not None or end_line is not None) and requested_start > 1),
                "has_more_after": bool((start_line is not None or end_line is not None) and requested_end < total_lines),
            },
        )

    def read_files(self, paths: list[str], *, max_chars_each: int = 12000) -> Observation:
        results: list[dict[str, Any]] = []
        blocked = 0
        failed = 0
        for path in paths[:20]:
            observation = self.read_file(path, max_chars=max_chars_each)
            if observation.status == "blocked":
                blocked += 1
            elif observation.status != "success":
                failed += 1
            results.append(
                {
                    "path": path,
                    "status": observation.status,
                    "output": observation.output,
                    "data": observation.data or {},
                    "risk_level": observation.risk_level,
                }
            )
        output_parts = []
        for item in results:
            output_parts.append(f"--- {item['path']} [{item['status']}]\n{item['output']}")
        status = "blocked" if blocked and blocked == len(results) else "failure" if failed and failed == len(results) else "success"
        return Observation(
            status,
            "\n\n".join(output_parts),
            {
                "count": len(results),
                "blocked": blocked,
                "failed": failed,
                "truncated_input": len(paths) > 20,
                "results": results,
            },
            risk_level="medium" if blocked else "low",
        )

    def search(self, pattern: str, *, limit: int = 80) -> Observation:
        if not pattern.strip():
            return Observation("failure", "search pattern is empty")
        preferred = self.config.get("search", "backend", default="auto")
        timeout = int(self.config.get("search", "timeout_seconds", default=30))
        backend = make_search_backend(self.root, self.safety, preferred=preferred, timeout_seconds=timeout)
        data = backend.search(pattern, limit=limit)
        return Observation(
            "success",
            "\n".join(data.get("matches", [])),
            {"pattern": pattern, **data},
        )

    def write_file(self, path: str, content: str, *, task_id: str, step: int) -> Observation:
        decision = self.safety.check_path(path, write=True)
        if not decision.allowed:
            return Observation("blocked", decision.reason, risk_level=decision.risk_level)
        target = (self.root / path).resolve()
        before_exists = target.exists()
        before = target.read_text(encoding="utf-8", errors="replace") if before_exists else ""
        if before == content:
            return Observation("success", "no changes", {"path": path, "changed": False})

        backup_rel = None
        backup_dir = self.paths.backups / task_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        if before_exists:
            backup_name = f"{step:03d}-{stable_hash(path)}-{target.name}.bak"
            backup_path = backup_dir / backup_name
            backup_path.write_text(before, encoding="utf-8")
            backup_rel = backup_path.relative_to(self.root).as_posix()
            self._cleanup_backups()

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        diff = unified_diff(before, content, f"a/{path}", f"b/{path}")
        return Observation(
            "success",
            shorten(diff, 12000),
            {
                "path": path,
                "changed": True,
                "before_exists": before_exists,
                "backup_path": backup_rel,
                "diff": diff,
            },
        )

    def _cleanup_backups(self) -> None:
        max_files = int(self.config.get("safety", "backup_max_files", default=500) or 0)
        max_bytes = int(self.config.get("safety", "backup_max_bytes", default=200_000_000) or 0)
        if max_files <= 0 and max_bytes <= 0:
            return
        files = [path for path in self.paths.backups.glob("**/*") if path.is_file()]
        files.sort(key=lambda item: item.stat().st_mtime)
        total = sum(path.stat().st_size for path in files)
        while files and ((max_files > 0 and len(files) > max_files) or (max_bytes > 0 and total > max_bytes)):
            oldest = files.pop(0)
            try:
                size = oldest.stat().st_size
                oldest.unlink()
                total -= size
            except OSError:
                continue

    def task_snapshot(self) -> dict[str, Any]:
        list_obs = self.list_files(limit=120)
        return {
            "root": str(self.root),
            "files": list_obs.output.splitlines(),
            "captured_at": utc_now(),
        }


class GitEnv:
    DIFF_EXCLUDE_PATHS = [
        ":(exclude)**/*.lock",
        ":(exclude)**/*-lock.json",
        ":(exclude)**/package-lock.json",
        ":(exclude)**/pnpm-lock.yaml",
        ":(exclude)**/yarn.lock",
        ":(exclude)**/*.svg",
        ":(exclude)**/*.map",
        ":(exclude)**/dist/**",
        ":(exclude)**/build/**",
    ]

    def __init__(self, config: Config):
        self.config = config
        self.root = config.paths.root
        self._repo_root_cache: Path | None = None

    def is_repo(self) -> bool:
        return self._repo_root() is not None

    def _git(self, args: list[str], *, timeout: int = 30) -> Observation:
        repo_root = self._repo_root()
        if repo_root is None:
            return Observation("failure", "not a git repository")
        try:
            result = run_process(["git", *args], cwd=repo_root, timeout=timeout)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return Observation("failure", str(exc))
        output = result.stdout.strip() or result.stderr.strip()
        return Observation("success" if result.returncode == 0 else "failure", output, {"returncode": result.returncode})

    def _repo_root(self) -> Path | None:
        if self._repo_root_cache is not None:
            return self._repo_root_cache
        try:
            result = run_process(["git", "rev-parse", "--show-toplevel"], cwd=self.root, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        repo_root = Path(result.stdout.strip()).resolve()
        try:
            self.root.resolve().relative_to(repo_root)
        except ValueError:
            return None
        self._repo_root_cache = repo_root
        return repo_root

    def _project_pathspec(self) -> str:
        repo_root = self._repo_root()
        if repo_root is None:
            return "."
        rel = self.root.resolve().relative_to(repo_root)
        return rel.as_posix() or "."

    def state(self) -> dict[str, Any]:
        if not self.is_repo():
            return {"is_repo": False}
        branch = self._git(["branch", "--show-current"]).output
        pathspec = self._project_pathspec()
        status = self._git(["status", "--short", "--", pathspec]).output
        return {"is_repo": True, "branch": branch, "status": status, "pathspec": pathspec}

    def diff(self) -> Observation:
        if not self.is_repo():
            return Observation("success", "", {"is_repo": False})
        return self._git(["diff", "--no-ext-diff", "--", *self._diff_pathspecs()], timeout=60)

    def diff_summary(self) -> dict[str, Any]:
        if not self.is_repo():
            return {"is_repo": False, "diff": ""}
        pathspec = self._project_pathspec()
        stat = self._git(["diff", "--stat", "--", *self._diff_pathspecs()]).output
        diff = self.diff().output
        return {"is_repo": True, "stat": stat, "diff": shorten(diff, 30000), "pathspec": pathspec}

    def _diff_pathspecs(self) -> list[str]:
        return [self._project_pathspec(), *self.DIFF_EXCLUDE_PATHS]


class ShellEnv:
    def __init__(self, config: Config, safety: SafetyPolicy):
        self.config = config
        self.root = config.paths.root
        self.safety = safety

    def run(self, command: str, *, timeout: int | None = None) -> Observation:
        timeout = int(timeout or self.config.get("runtime", "shell_timeout_seconds", default=120))
        decision = self.safety.check_command(command)
        if not decision.allowed:
            return Observation("blocked", decision.reason, risk_level=decision.risk_level)
        use_shell = bool(self.config.get("shell", "allow_shell_features", default=False)) and _has_shell_features(command)
        if not use_shell:
            try:
                parts = shlex.split(command)
            except ValueError as exc:
                return Observation("blocked", f"invalid shell quoting: {exc}", risk_level="medium")
            run_args: str | list[str] = parts
        else:
            run_args = command
        try:
            result = _run_subprocess_isolated(
                run_args,
                shell=use_shell,
                cwd=str(self.root),
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            return Observation("failure", f"command executable not found: {exc}", {"command": command})
        except subprocess.TimeoutExpired as exc:
            return Observation("failure", f"command timed out after {timeout}s: {exc}", {"command": command})
        output = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        return Observation(
            "success" if result.returncode == 0 else "failure",
            shorten(output, 20000),
            {"command": command, "returncode": result.returncode, "shell": use_shell},
        )


def _run_subprocess_isolated(
    run_args: str | list[str],
    *,
    shell: bool,
    cwd: str,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "shell": shell,
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "close_fds": True,
    }
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(run_args, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            stdout = exc.output if isinstance(exc.output, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            _close_process_pipes(process)
        raise subprocess.TimeoutExpired(run_args, timeout, output=stdout, stderr=stderr) from exc
    return subprocess.CompletedProcess(run_args, process.returncode, stdout, stderr)


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except Exception:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdout, process.stderr, process.stdin):
        if pipe:
            try:
                pipe.close()
            except OSError:
                pass


class TestEnv:
    __test__ = False

    def __init__(self, config: Config, shell: ShellEnv):
        self.config = config
        self.root = config.paths.root
        self.shell = shell

    def detect_commands(self) -> list[str]:
        configured = self.config.get("runtime", "default_test_commands", default=[])
        if configured:
            return list(configured)
        return inspect_project(self.root).test_commands

    def run(self, commands: list[str] | None = None) -> list[dict[str, Any]]:
        selected = commands if commands is not None else self.detect_commands()
        results: list[dict[str, Any]] = []
        for command in selected:
            timeout = int(self.config.get("runtime", "test_timeout_seconds", default=180))
            observation = self.shell.run(command, timeout=timeout)
            results.append(observation.to_dict())
        return results


class ProjectEnv:
    def __init__(self, config: Config, fs: FileSystemEnv, git: GitEnv, tests: TestEnv):
        self.config = config
        self.root = config.paths.root
        self.fs = fs
        self.git = git
        self.tests = tests

    def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        project_map = build_project_map(self.config, max_dirs=60, max_files=80, refresh=refresh)
        return {
            "filesystem": {
                "root": str(self.root),
                "project_map": project_map,
                "files": project_map.get("root_files", []),
                "captured_at": project_map.get("generated_at"),
            },
            "git": self.git.state(),
            "tests_detected": self.tests.detect_commands(),
        }

    def rollback_task(self, trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        restored: list[dict[str, Any]] = []
        actions = list(trajectory.get("actions", []))
        for action in reversed(actions):
            if action.get("action_type") != "edit_file":
                continue
            obs = action.get("observation", {})
            data = obs.get("data", {})
            rel = data.get("path")
            if not rel:
                continue
            target = (self.config.paths.root / rel).resolve()
            backup_rel = data.get("backup_path")
            before_exists = data.get("before_exists", True)
            if backup_rel:
                backup = self.config.paths.root / backup_rel
                if backup.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(backup, target)
                    restored.append({"path": rel, "mode": "restored_backup"})
            elif not before_exists and target.exists():
                target.unlink()
                restored.append({"path": rel, "mode": "deleted_new_file"})
        return restored
