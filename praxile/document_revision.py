"""Digest-guarded document replacement, with binary rollback evidence."""
import hashlib
import os
from pathlib import Path
import tempfile
import uuid

from .documents import create_docx, create_xlsx
from .environment import Observation
from .utils import file_lock


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path):
    with path.open("rb") as handle:
        data = handle.read(10_000_001)
    if len(data) > 10_000_000:
        raise ValueError("document size limit")
    return data


def _paths(config, safety, path, kind=None):
    root = config.paths.root.resolve(strict=True)
    target = root / path
    storage = root / ".praxile/backups/document-revisions"
    suffixes = {"." + kind} if kind is not None else {".docx", ".xlsx"}
    if (not safety.check_path(path, write=True).allowed or target.suffix.lower() not in suffixes
            or not target.is_relative_to(root) or target.resolve(strict=True) != target
            or not target.is_file() or storage.resolve() != storage):
        raise ValueError("unsafe document revision path")
    storage.mkdir(parents=True, exist_ok=True)
    lock = storage / (_digest(target.relative_to(root).as_posix().encode()) + ".revision")
    if lock.with_name(lock.name + ".lock").is_symlink():
        raise ValueError("unsafe revision lock")
    return root, target, storage, lock


def revise_docx(config, safety, path, spec, expected_sha256):
    return _revise(config, safety, path, spec, expected_sha256, "docx", create_docx)


def revise_xlsx(config, safety, path, spec, expected_sha256):
    return _revise(config, safety, path, spec, expected_sha256, "xlsx", create_xlsx)


def _revise(config, safety, path, spec, expected_sha256, kind, create):
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)):
        return Observation("failure", "expected_sha256 must be the current document SHA-256")
    try:
        root, target, storage, lock = _paths(config, safety, path, kind)
        before = _read(target)
        if _digest(before) != expected_sha256:
            return Observation("blocked", "Document changed; inspect the current version before revising")
        # Render completely before taking the publication lock or backing up.
        with tempfile.TemporaryDirectory(prefix=".praxile-revision-", dir=target.parent) as temporary:
            staged = Path(temporary) / ("revision." + kind)
            rendered = create(config, safety, staged.relative_to(root).as_posix(), spec)
            if rendered.status != "success":
                return rendered
            with file_lock(lock):
                _paths(config, safety, path, kind)
                if _digest(_read(target)) != expected_sha256:
                    return Observation("blocked", "Document changed during rendering; revision not published")
                backup = storage / (uuid.uuid4().hex + "." + kind)
                with backup.open("xb") as handle:
                    handle.write(before)
                    handle.flush()
                    os.fsync(handle.fileno())
                # A non-cooperating editor can still race the final rename.
                if _digest(_read(target)) != expected_sha256:
                    return Observation("blocked", "Document changed before publication; revision not published")
                staged.replace(target)
            return Observation("success", f"Rebuilt {kind.upper()} with backup; recheck layout and factual quality.", {
                "path": path, "changed": True, "before_exists": True,
                "before_sha256": expected_sha256, "sha256": rendered.data["sha256"],
                "size_bytes": rendered.data["size_bytes"], "backup_path": backup.relative_to(root).as_posix()})
    except (OSError, ValueError, TimeoutError):
        return Observation("failure", f"Unable to revise {kind.upper()}; verify path, permissions and current digest")


def rollback_revision(config, safety, data):
    path = data.get("path")
    if not isinstance(path, str):
        return {"path": path, "mode": "blocked"}
    try:
        root, target, storage, lock = _paths(config, safety, path)
        backup = root / data.get("backup_path", "")
        if backup.parent != storage or backup.resolve(strict=True) != backup or not backup.is_file():
            raise ValueError("unsafe backup")
        original = _read(backup)
        if _digest(original) != data.get("before_sha256"):
            return {"path": path, "mode": "backup_digest_mismatch"}
        with file_lock(lock):
            _paths(config, safety, path)
            if _digest(_read(target)) != data.get("sha256"):
                return {"path": path, "mode": "skipped_modified"}
            with tempfile.TemporaryDirectory(prefix=".praxile-restore-", dir=target.parent) as directory:
                staged = Path(directory) / ("original" + target.suffix.lower())
                with staged.open("xb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                if _digest(_read(target)) != data.get("sha256"):
                    return {"path": path, "mode": "skipped_modified"}
                staged.replace(target)
        return {"path": path, "mode": "restored_backup"}
    except (OSError, ValueError, TypeError, TimeoutError):
        return {"path": path, "mode": "blocked"}
