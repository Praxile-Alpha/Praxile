"""Content snapshots of local benchmark inputs; no credential or rubric export."""
import hashlib
import os
from pathlib import Path
import stat


def snapshot_inputs(job):
    """Hash task trees and explicit worker wheels, never their contents in reports.

    This detects changes between arms, not writes during an executing arm.
    The bound prevents accidentally hashing an unbounded workspace/device.
    """
    remaining = 2 * 1024**3
    entries = 0

    def file_record(path):
        nonlocal remaining, entries
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("local benchmark input must be a regular file")
        entries += 1
        if entries > 50000 or before.st_size > remaining:
            raise ValueError("local input snapshot exceeds 50000 files or 2 GiB")
        hasher = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("local input replaced during snapshot")
            while chunk := handle.read(1024 * 1024):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError("local input snapshot exceeds 2 GiB")
                hasher.update(chunk)
        after = path.lstat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
            raise ValueError("local input changed during snapshot")
        return {"sha256": hasher.hexdigest(), "size": before.st_size, "mode": stat.S_IMODE(before.st_mode)}

    def physical(value):
        path = Path(value).absolute()
        if path.resolve(strict=True) != path:
            raise ValueError("local input must not contain symlink components")
        return path

    tasks = []
    def walk_error(error):
        raise error
    for task in job["tasks"]:
        root = physical(task["path"])
        if not root.is_dir():
            raise ValueError("task input must be a directory")
        records = {}
        for directory, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
            directories.sort()
            for name in directories:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("symlink directory in task input")
            folder = Path(directory)
            records[str(folder.relative_to(root)) + "/"] = {"mode": stat.S_IMODE(folder.stat().st_mode)}
            if len(records) > 50000:
                raise ValueError("too many task input entries")
            for name in sorted(files):
                path = folder / name
                records[str(path.relative_to(root))] = file_record(path)
        tasks.append({"path": str(root), "entries": records})
    kwargs = job["agents"][0]["kwargs"]
    wheels = []
    for item in [{"path": kwargs["wheel_path"], "sha256": kwargs["wheel_sha256"]},
                 *kwargs.get("dependency_wheels", [])]:
        path = physical(item["path"])
        record = file_record(path)
        if record["sha256"] != item["sha256"]:
            raise ValueError("worker wheel differs from configured digest")
        wheels.append({"path": str(path), **record})
    return {"schema_version": "praxile.native_local_inputs.v1", "tasks": tasks, "wheels": wheels}
