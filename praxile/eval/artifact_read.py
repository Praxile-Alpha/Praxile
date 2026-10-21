"""Bounded descriptor-relative reads for local lease artifacts (POSIX only)."""
from contextlib import ExitStack
import os
from pathlib import PurePosixPath
import stat


DEFAULT_ARTIFACT_BYTES = 32 * 1024**2


def read_artifact(root, relative_path, *, root_identity, max_bytes=DEFAULT_ARTIFACT_BYTES):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 256 * 1024**2:
        raise ValueError("artifact read limit must be 1..256 MiB")
    if not isinstance(relative_path, str) or any(ord(c) < 32 for c in relative_path):
        raise ValueError("artifact path must be normalized and relative")
    path = PurePosixPath(relative_path)
    if (not path.parts or "\\" in relative_path or path.is_absolute()
            or ".." in path.parts or str(path) != relative_path):
        raise ValueError("artifact path must be normalized and relative")
    if (os.name != "posix" or os.open not in os.supports_dir_fd
            or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))):
        raise NotImplementedError("artifact collection requires descriptor-relative POSIX reads")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        with ExitStack() as stack:
            parent = os.open(root, flags | os.O_DIRECTORY)
            stack.callback(os.close, parent)
            info = os.fstat(parent)
            if (info.st_dev, info.st_ino) != root_identity:
                raise ValueError("workspace root replaced after lease creation or reset")
            for component in path.parts[:-1]:
                parent = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent)
                stack.callback(os.close, parent)
            fd = os.open(path.name, flags, dir_fd=parent)
            stack.callback(os.close, fd)
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("artifact must be a regular file without additional hard links")
            if before.st_size > max_bytes:
                raise ValueError("artifact exceeds byte limit")
            content = bytearray()
            while chunk := os.read(fd, min(1024**2, max_bytes + 1 - len(content))):
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("artifact exceeds byte limit")
            after = os.fstat(fd)
            linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            signature = lambda value: tuple(getattr(value, field) for field in fields)
            if signature(before) != signature(after) or signature(after) != signature(linked) or len(content) != after.st_size:
                raise ValueError("artifact changed during collection")
            return bytes(content)
    except OSError as error:
        raise ValueError("artifact cannot be collected without following links") from error
