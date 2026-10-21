"""Validate offline worker requirements without importing Harbor or executing code."""
import hashlib
from pathlib import Path
import re

from ..capability_probe import PROFILES


def validate_requirements(dependencies=None, capabilities=None, *, worker_filename=None):
    if capabilities is not None and (not isinstance(capabilities, list)
            or any(not isinstance(item, str) or item not in PROFILES for item in capabilities)
            or len(set(capabilities)) != len(capabilities)):
        raise ValueError("required_capabilities must contain unique supported versioned profiles")
    if dependencies is not None and not isinstance(dependencies, list):
        raise ValueError("dependency_wheels must be a list")
    records = []
    names = {worker_filename}
    for item in dependencies or []:
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or not isinstance(item["path"], str) or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("invalid dependency wheel record")
        path = Path(item["path"]).resolve(strict=True)
        if (not path.is_file() or not re.fullmatch(r"[A-Za-z0-9_.+!-]+\.whl", path.name)
                or path.name in names or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
            raise ValueError("dependency wheel digest or filename mismatch")
        names.add(path.name)
        records.append({"path": str(path), "sha256": item["sha256"]})
    return sorted(records, key=lambda item: Path(item["path"]).name), sorted(capabilities or [])


def identity(dependencies, capabilities):
    return {"dependencies": [{"filename": Path(item["path"]).name, "sha256": item["sha256"]}
                             for item in dependencies], "required_capabilities": capabilities}
