"""Freeze only explicitly declared local public inputs, never a task directory."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import tempfile

from .artifact_read import DEFAULT_ARTIFACT_BYTES, read_artifact
from .contracts import InputArtifact, _relative

MAX_INPUT_BYTES = 128 * 1024**2
MAX_INPUT_FILES = 256


def _validate(inputs):
    if not isinstance(inputs, tuple) or len(inputs) > MAX_INPUT_FILES:
        raise ValueError("public inputs must be a tuple of at most 256 artifacts")
    paths = set()
    for item in inputs:
        if not isinstance(item, InputArtifact):
            raise ValueError("public inputs must be InputArtifact records")
        _relative(item.source_ref)
        if any(ord(c) < 32 for c in item.path + item.source_ref):
            raise ValueError("public input paths must not contain control characters")
        folded = item.path.casefold()
        if folded in paths:
            raise ValueError("duplicate public input path")
        paths.add(folded)
    for path in paths:
        if any(str(parent) in paths for parent in PurePosixPath(path).parents):
            raise ValueError("public input file/directory paths overlap")


def _identity(root):
    info = root.stat()
    return info.st_dev, info.st_ino


def _checked_bytes(root, path, digest, identity, remaining):
    data = read_artifact(root, path, root_identity=identity,
                         max_bytes=min(DEFAULT_ARTIFACT_BYTES, remaining))
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("public input digest mismatch")
    return data


def freeze_public_inputs(source_root: Path, inputs: tuple[InputArtifact, ...], destination: Path):
    _validate(inputs)
    source_root = source_root.resolve(strict=True)
    identity = _identity(source_root)
    content = []
    remaining = MAX_INPUT_BYTES
    for item in inputs:
        if remaining <= 0:
            raise ValueError("public input byte budget exceeded")
        data = _checked_bytes(source_root, item.source_ref, item.content_digest, identity, remaining)
        remaining -= len(data)
        content.append((item.path, data))
    # No partially populated published snapshot on validation or write failure.
    if destination.exists() or destination.is_symlink():
        raise ValueError("public input snapshot already exists")
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".inputs-") as staging:
        stage = Path(staging)
        for name, data in content:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o444)
        for directory in stage.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
        stage.chmod(0o755)
        stage.rename(destination)
    return _identity(destination)


def verify_public_inputs(root: Path, inputs: tuple[InputArtifact, ...], identity):
    _validate(inputs)
    if root.is_symlink() or _identity(root) != identity:
        raise ValueError("public input snapshot replaced")
    expected_files = {item.path for item in inputs}
    expected_dirs = {str(parent) for item in inputs for parent in PurePosixPath(item.path).parents
                     if str(parent) != "."}
    actual_files, actual_dirs = set(), set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("public input snapshot contains a link")
        relative = path.relative_to(root).as_posix()
        (actual_dirs if path.is_dir() else actual_files).add(relative)
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise ValueError("public input snapshot contents changed")
    remaining = MAX_INPUT_BYTES
    for item in inputs:
        if remaining <= 0:
            raise ValueError("public input byte budget exceeded")
        remaining -= len(_checked_bytes(root, item.path, item.content_digest, identity, remaining))
