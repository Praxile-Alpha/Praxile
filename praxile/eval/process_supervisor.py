"""Container-side deadline for native benchmark workers (POSIX only)."""
from __future__ import annotations

import argparse
import math
import os
import signal
import subprocess
import time
from pathlib import Path

from ..utils import write_json


class _Terminated(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


def supervise(argv: list[str], *, timeout: float, status_path: Path,
              cancel_file: Path | None = None) -> int:
    if os.name != "posix":
        raise RuntimeError("native worker supervision requires POSIX")
    if not argv or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("a command and finite positive timeout are required")
    process = None
    record = {"schema_version": "praxile.worker_supervision.v1", "status": "starting",
              "timeout_seconds": timeout, "returncode": None}
    previous = {}
    pending_signal = None
    def interrupted(signum, frame):
        nonlocal pending_signal
        if process is None:
            pending_signal = signum
            return
        raise _Terminated(signum)
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, interrupted)
        write_json(status_path, record)
        if cancel_file is not None and cancel_file.exists():
            raise _Terminated(signal.SIGTERM)
        # Defer interruption until assignment without passing a blocked signal
        # mask into the worker or its descendants.
        process = subprocess.Popen(argv, start_new_session=True)
        if pending_signal is not None:
            raise _Terminated(pending_signal)
        record["status"] = "running"
        write_json(status_path, record)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if cancel_file is not None and cancel_file.exists():
                    raise _Terminated(signal.SIGTERM)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                try:
                    code = process.wait(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            record.update(status="completed" if code == 0 else "failed", returncode=code)
        except subprocess.TimeoutExpired:
            code = 124
            record.update(status="timed_out", returncode=code)
    except _Terminated as error:
        code = 128 + error.signum
        record.update(status="cancelled", returncode=code)
    except BaseException:
        record["status"] = "supervisor_error"
        raise
    finally:
        # Ignore a second termination signal while reaping the owned group.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            write_json(status_path, record)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    raise SystemExit(supervise(argv, timeout=args.timeout, status_path=args.status,
                              cancel_file=args.cancel_file))


if __name__ == "__main__":
    main()
