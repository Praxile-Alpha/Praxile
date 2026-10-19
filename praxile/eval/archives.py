"""Bounded, non-executing extraction of pinned benchmark datasets."""
from __future__ import annotations

import hashlib
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from ..utils import file_lock
from .v2.schema import EvalSchemaError


def extract_dataset(archive: Path, destination: Path, *, sha256: str,
                    max_bytes: int = 4_000_000_000, max_members: int = 100_000) -> Path:
    """Publish exactly one expected dataset directory; never extract nested archives.

    Limits bound expanded payload bytes and member count. Untrusted content is
    not executed. This does not audit Dockerfiles or scripts inside the dataset.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise EvalSchemaError("a pinned SHA-256 is required")
    if type(max_bytes) is not int or type(max_members) is not int or min(max_bytes, max_members) <= 0:
        raise EvalSchemaError("archive limits must be positive integers")
    if destination.name in {"", ".", ".."}:
        raise EvalSchemaError("destination must name a dataset directory")
    destination = destination.parent.resolve(strict=True) / destination.name
    with file_lock(destination.parent / (destination.name + ".extract"), timeout_seconds=120):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("dataset destination already exists")
        # Reuse the opened file after hashing, avoiding replacement between opens.
        with archive.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != sha256:
                raise EvalSchemaError("archive digest mismatch")
            handle.seek(0)
            with tempfile.TemporaryDirectory(prefix=".dataset-staging-", dir=destination.parent) as stage:
                staging = Path(stage)
                seen: set[str] = set()
                total = 0
                count = 0
                with tarfile.open(fileobj=handle, mode="r|gz") as bundle:
                    for member in bundle:
                        count += 1
                        if count > max_members:
                            raise EvalSchemaError("archive member limit exceeded")
                        name = member.name.rstrip("/") if member.isdir() else member.name
                        path = PurePosixPath(name)
                        if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
                                or ":" in name or path.as_posix() != name
                                or path.parts[0] != destination.name):
                            raise EvalSchemaError("archive member escapes expected dataset root")
                        if name in seen:
                            raise EvalSchemaError("duplicate archive member")
                        seen.add(name)
                        target = staging.joinpath(*path.parts)
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                        elif member.isfile():
                            total += member.size
                            if member.size < 0 or total > max_bytes:
                                raise EvalSchemaError("archive expanded byte limit exceeded")
                            target.parent.mkdir(parents=True, exist_ok=True)
                            source = bundle.extractfile(member)
                            if source is None:
                                raise EvalSchemaError("archive file payload is unavailable")
                            with source, target.open("xb") as output:
                                shutil.copyfileobj(source, output, length=1024 * 1024)
                            target.chmod(member.mode & 0o777)
                        else:
                            raise EvalSchemaError("archive links and special files are forbidden")
                root = staging / destination.name
                if not root.is_dir() or not count:
                    raise EvalSchemaError("archive contains no dataset directory")
                root.rename(destination)
    return destination
