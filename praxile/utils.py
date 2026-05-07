from __future__ import annotations

import difflib
import builtins
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(value: str, *, max_length: int = 64) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", value)
    value = value.strip("-")
    if not value:
        value = "untitled"
    return value[:max_length].strip("-") or "untitled"


def stable_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with file_lock(path):
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)


def append_jsonl(path: Path, data: dict[str, Any], *, sync: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False) + "\n")
            handle.flush()
            if sync:
                os.fsync(handle.fileno())


def _input_is_mocked() -> bool:
    module = getattr(builtins.input, "__module__", "builtins")
    return module != "builtins"


def safe_input(prompt: str) -> str:
    if (env_truthy("CI") or env_truthy("PRAXILE_NON_INTERACTIVE") or not sys.stdin.isatty()) and not _input_is_mocked():
        raise RuntimeError(f"interactive input requested in non-interactive environment: {prompt}")
    return builtins.input(prompt)


@contextmanager
def file_lock(path: Path, *, timeout_seconds: float = 5.0, poll_seconds: float = 0.05):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":  # pragma: no cover - exercised on Windows
        with lock_path.open("a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            import msvcrt

            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out waiting for file lock: {lock_path}") from exc
                    time.sleep(poll_seconds)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        return
    try:
        import fcntl
    except ImportError:  # pragma: no cover - defensive fallback for unusual runtimes
        with _atomic_file_lock(lock_path, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds):
            yield
        return
    with lock_path.open("a", encoding="utf-8") as lock:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for file lock: {lock_path}") from exc
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


@contextmanager
def _atomic_file_lock(lock_path: Path, *, timeout_seconds: float = 30.0, poll_seconds: float = 0.05):
    token_path = lock_path.with_suffix(lock_path.suffix + ".held")
    deadline = time.monotonic() + timeout_seconds
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(token_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for file lock: {lock_path}")
            time.sleep(poll_seconds)
    try:
        os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        yield
    finally:
        os.close(fd)
        try:
            token_path.unlink()
        except FileNotFoundError:
            pass


def unified_diff(before: str, after: str, fromfile: str, tofile: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )


def run_process(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 30,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        timeout=timeout,
        capture_output=True,
        text=text,
        check=False,
    )


def shorten(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 40] + "\n...[truncated]...\n" + value[-20:]


def indent_block(value: str, prefix: str = "  ") -> str:
    return textwrap.indent(value.rstrip(), prefix)


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}
