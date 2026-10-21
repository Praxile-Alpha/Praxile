"""Offline worker artifact checks, without importing optional Harbor packages."""
import hashlib
from pathlib import Path
import re
import zipfile


def check_worker_wheel(path: Path, digest: str):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("worker SHA-256 must be 64 lowercase hex characters")
    if path.is_symlink() or not path.is_file() or path.suffix != ".whl":
        raise ValueError("worker must be a regular wheel file")
    if path.stat().st_size > 256 * 1024**2:
        raise ValueError("worker wheel exceeds size limit")
    with path.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != digest:
        raise ValueError("worker wheel digest mismatch")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("worker wheel contains duplicate entries")
            required = {"praxile/eval/native_worker.py", "praxile/eval/process_supervisor.py"}
            if not required.issubset(names):
                raise ValueError("worker wheel lacks native worker or process supervisor")
    except zipfile.BadZipFile as error:
        raise ValueError("worker is not a valid wheel archive") from error
