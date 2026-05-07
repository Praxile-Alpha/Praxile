from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Protocol

from .constants import DEFAULT_EXCLUDES
from .security import SafetyPolicy
from .utils import run_process


class SearchBackend(Protocol):
    name: str

    def search(self, pattern: str, *, limit: int = 80) -> dict[str, Any]:
        ...


class PythonSearchBackend:
    name = "python"

    def __init__(self, root: Path, safety: SafetyPolicy, *, timeout_seconds: int = 30):
        self.root = root
        self.safety = safety
        self.timeout_seconds = timeout_seconds

    def search(self, pattern: str, *, limit: int = 80) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + max(0.001, float(self.timeout_seconds or 30))
        matches: list[str] = []
        skipped: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        protected_skipped = 0
        timed_out = False
        lowered = pattern.lower()
        for current, dirs, filenames in os.walk(self.root):
            if time.monotonic() >= deadline:
                timed_out = True
                errors.append({"path": "", "error": f"search timed out after {self.timeout_seconds}s"})
                break
            current_path = Path(current)
            rel_parts = set(current_path.relative_to(self.root).parts) if current_path != self.root else set()
            if rel_parts & DEFAULT_EXCLUDES:
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name not in DEFAULT_EXCLUDES]
            for filename in filenames:
                if time.monotonic() >= deadline:
                    timed_out = True
                    errors.append({"path": "", "error": f"search timed out after {self.timeout_seconds}s"})
                    break
                path = current_path / filename
                rel = path.relative_to(self.root).as_posix()
                decision = self.safety.check_path(rel)
                if not decision.allowed:
                    protected_skipped += 1
                    continue
                try:
                    raw = path.read_bytes()
                    if b"\x00" in raw[:4096]:
                        skipped.append({"path": rel, "reason": "binary_file"})
                        continue
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    errors.append({"path": rel, "error": exc.__class__.__name__})
                    continue
                except OSError as exc:
                    errors.append({"path": rel, "error": exc.__class__.__name__})
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if time.monotonic() >= deadline:
                        timed_out = True
                        errors.append({"path": rel, "error": f"search timed out after {self.timeout_seconds}s"})
                        break
                    if lowered in line.lower():
                        matches.append(f"{rel}:{lineno}:{line}")
                        if len(matches) >= limit:
                            break
                if timed_out or len(matches) >= limit:
                    break
            if timed_out or len(matches) >= limit:
                break
        return {
            "backend": self.name,
            "matches": matches,
            "skipped": skipped[:20],
            "errors": errors[:20],
            "protected_skipped_count": protected_skipped,
            "truncated": timed_out or len(matches) >= limit,
            "timed_out": timed_out,
            "timeout_seconds": self.timeout_seconds,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


class RipgrepSearchBackend:
    name = "ripgrep"

    def __init__(self, root: Path, safety: SafetyPolicy, *, timeout_seconds: int = 30):
        self.root = root
        self.safety = safety
        self.timeout_seconds = timeout_seconds

    @classmethod
    def available(cls) -> bool:
        return bool(shutil.which("rg"))

    def search(self, pattern: str, *, limit: int = 80) -> dict[str, Any]:
        started = time.monotonic()
        globs: list[str] = []
        for excluded in sorted(DEFAULT_EXCLUDES):
            globs.extend(["--glob", f"!{excluded}/**"])
        globs.extend(
            [
                "--glob",
                "!.env",
                "--glob",
                "!.env.*",
                "--glob",
                "!**/*.pem",
                "--glob",
                "!**/*.key",
                "--glob",
                "!**/.aws/**",
                "--glob",
                "!**/.ssh/**",
                "--glob",
                "!**/*secret*",
                "--glob",
                "!**/*credential*",
            ]
        )
        args = [
            "rg",
            "--line-number",
            "--hidden",
            "--color",
            "never",
            "--max-count",
            str(limit),
            *globs,
            "--",
            pattern,
            ".",
        ]
        try:
            result = run_process(args, cwd=self.root, timeout=self.timeout_seconds)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return PythonSearchBackend(self.root, self.safety, timeout_seconds=self.timeout_seconds).search(pattern, limit=limit) | {
                "fallback_reason": exc.__class__.__name__
            }
        matches: list[str] = []
        protected_skipped = 0
        errors: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            rel, rest = _split_rg_line(line)
            if rel is None:
                continue
            decision = self.safety.check_path(rel)
            if not decision.allowed:
                protected_skipped += 1
                continue
            matches.append(f"{rel}:{rest}")
            if len(matches) >= limit:
                break
        for line in result.stderr.splitlines()[:20]:
            if line.strip():
                errors.append({"path": "", "error": line.strip()})
        return {
            "backend": self.name,
            "matches": matches,
            "skipped": [],
            "errors": errors,
            "protected_skipped_count": protected_skipped,
            "truncated": len(matches) >= limit,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "returncode": result.returncode,
        }


def make_search_backend(
    root: Path,
    safety: SafetyPolicy,
    preferred: str = "auto",
    *,
    timeout_seconds: int = 30,
) -> SearchBackend:
    if preferred in {"auto", "rg", "ripgrep"} and RipgrepSearchBackend.available():
        return RipgrepSearchBackend(root, safety, timeout_seconds=timeout_seconds)
    return PythonSearchBackend(root, safety, timeout_seconds=timeout_seconds)


def _split_rg_line(line: str) -> tuple[str | None, str]:
    parts = line.split(":", 2)
    if len(parts) < 3:
        return None, line
    rel = parts[0]
    if rel.startswith("./"):
        rel = rel[2:]
    return rel, f"{parts[1]}:{parts[2]}"
