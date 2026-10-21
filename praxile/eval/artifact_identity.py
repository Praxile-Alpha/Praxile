"""Compare two artifact observations; never attest verifier input or quality."""
from pathlib import PurePosixPath
import re


def compare_inventory(inventory, retention, directory):
    result = {"schema_version": "praxile.artifact_identity.v1", "status": "unknown",
              "provenance_attested": False, "verifier_input_verified": False,
              "content_verified": False}
    if not isinstance(inventory, dict):
        return dict(result, reason="inventory_missing")
    if inventory.get("schema_version") != "praxile.artifact_inventory.v1":
        return dict(result, reason="unsupported_inventory")
    if inventory.get("complete") is not True:
        return dict(result, reason="inventory_incomplete")
    if not isinstance(directory, str) or inventory.get("directory") != directory:
        return dict(result, reason="directory_mismatch")
    if retention.get("status") not in {"retained", "empty"}:
        return dict(result, reason="retention_unavailable")
    rows = inventory.get("files")
    if not isinstance(rows, list) or len(rows) > 200:
        return dict(result, reason="invalid_inventory")
    observed = {}
    for row in rows:
        if not isinstance(row, dict):
            return dict(result, reason="invalid_inventory")
        path, size, digest = row.get("path"), row.get("size_bytes"), row.get("sha256")
        if (not isinstance(path, str) or "\\" in path or any(ord(c) < 32 for c in path)
                or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
                or str(PurePosixPath(path)) != path or type(size) is not int or size < 0
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            return dict(result, reason="invalid_inventory")
        try:
            relative = PurePosixPath(path).relative_to(directory).as_posix()
        except ValueError:
            return dict(result, reason="invalid_inventory")
        if relative == "." or relative in observed:
            return dict(result, reason="invalid_inventory")
        observed[relative] = (size, digest)
    retained = {row["path"]: (row["size"], row["sha256"]) for row in retention["files"]}
    missing = sorted(observed.keys() - retained.keys())
    added = sorted(retained.keys() - observed.keys())
    changed = sorted(path for path in observed.keys() & retained.keys()
                     if observed[path] != retained[path])
    return dict(result, status="mismatch" if missing or added or changed else "matched",
                observed_count=len(observed), retained_count=len(retained),
                missing=missing, added=added, changed=changed)
