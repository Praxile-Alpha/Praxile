"""Verify the declared artifact set before releasing any files to a judge."""
import hashlib
import json


def verify_artifact_set(task, records, read, *, source_path, output_path):
    if len(task.inputs) > 256 or len(task.outputs) > 256:
        raise ValueError("review artifact count exceeds limit")
    remaining = 128 * 1024**2
    selected, inventory = {}, []

    def verify(path, digest, size=None):
        nonlocal remaining
        chosen = path in {"inputs/" + source_path, "outputs/" + output_path}
        limit = min(1024**2 if chosen else 32 * 1024**2, remaining)
        raw = read(path, max_bytes=limit)
        remaining -= len(raw)
        actual = hashlib.sha256(raw).hexdigest()
        if actual != digest or (size is not None and len(raw) != size):
            raise ValueError("execution artifact digest mismatch")
        inventory.append({"path": path, "status": "verified", "bytes": len(raw), "sha256": actual})
        if chosen:
            selected[path] = raw

    for source in task.inputs:
        verify("inputs/" + source.path, source.content_digest.removeprefix("sha256:"))
    by_path = {record["path"]: record for record in records}
    for output in task.outputs:
        record = by_path[output.path]
        if record.get("status") == "unavailable" and not output.required:
            inventory.append({"path": "outputs/" + output.path, "status": "unavailable"})
            continue
        if (record.get("status") != "collected" or type(record.get("bytes")) is not int
                or record["bytes"] < 0):
            raise ValueError("invalid collected output record")
        verify("outputs/" + output.path, record.get("sha256"), record["bytes"])
    digest = hashlib.sha256(json.dumps(sorted(inventory, key=lambda item: item["path"]),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return selected["inputs/" + source_path], selected["outputs/" + output_path], digest
