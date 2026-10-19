"""Control-plane audit of explicitly requested native output artifacts."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat


def audit_output(root, trial):
    """Record retention, not correctness or proof of pre-verifier contents."""
    result = {"schema_version": "praxile.output_retention.v1", "status": "not_requested",
              "files": [], "content_verified": False}
    agent = (trial.get("config") or {}).get("agent") or {}
    output = (agent.get("kwargs") or {}).get("output_directory")
    if output is None:
        return result
    if (not isinstance(output, str) or not PurePosixPath(output).parts
            or PurePosixPath(output).is_absolute() or ".." in PurePosixPath(output).parts
            or "\\" in output or any(ord(char) < 32 for char in output)):
        raise ValueError("invalid artifact output directory")
    root = Path(root).resolve(strict=True)
    manifest = root / "artifacts/manifest.json"
    result["status"] = "missing_manifest"
    if not manifest.exists() and not manifest.is_symlink():
        return result
    if manifest.resolve() != manifest or not manifest.is_file() or manifest.stat().st_size > 1024**2:
        raise ValueError("invalid artifact manifest path or size")
    raw = manifest.read_bytes()
    result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    entries = json.loads(raw)
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("invalid artifact manifest")
    source = str(PurePosixPath("/workspace") / output)
    matches = [entry for entry in entries if entry.get("source") == source and entry.get("service") in (None, "main")]
    result["status"] = "not_collected"
    if not matches:
        return result
    if len(matches) != 1:
        raise ValueError("duplicate output artifact entry")
    entry = matches[0]
    expected = "artifacts/" + source.lstrip("/")
    if entry.get("destination") != expected or entry.get("type") != "directory":
        raise ValueError("unexpected output artifact destination or type")
    if entry.get("status") not in {"ok", "empty"}:
        return result
    target = root / expected
    if not target.exists() and not target.is_symlink():
        result["status"] = "missing_files"
        return result
    if target.resolve() != target or not target.is_dir():
        raise ValueError("invalid output artifact directory")
    remaining = 256 * 1024**2
    count = 0
    def onerror(error):
        raise error
    for directory, dirs, files in os.walk(target, followlinks=False, onerror=onerror):
        count += len(dirs) + len(files)
        if count > 10000:
            raise ValueError("artifact entry limit exceeded")
        if any((Path(directory) / name).is_symlink() for name in dirs):
            raise ValueError("symlink in output artifacts")
        for name in sorted(files):
            path = Path(directory) / name
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > remaining:
                raise ValueError("artifact must be regular and within 256 MiB budget")
            hasher = hashlib.sha256()
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("artifact replaced during audit")
                while chunk := handle.read(1024**2):
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise ValueError("artifact byte limit exceeded")
                    hasher.update(chunk)
            after = path.lstat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("artifact changed during audit")
            result["files"].append({"path": path.relative_to(target).as_posix(),
                                    "size": before.st_size, "sha256": hasher.hexdigest()})
    result["files"].sort(key=lambda item: item["path"])
    result["status"] = "retained" if result["files"] else "empty"
    result["files_sha256"] = hashlib.sha256(json.dumps(result["files"], sort_keys=True).encode()).hexdigest()
    return result
