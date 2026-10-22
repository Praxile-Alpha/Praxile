"""Frozen candidate admission for held-out tasks; no evaluator or update channel."""
import hashlib

from .native_context import load_candidate
from .v2.candidate import ContextCandidate
from .v2.schema import canonical_json


def validate_protocol_shape(protocol):
    fields = {"schema_version", "development_task_ids", "heldout_task_ids",
              "candidate_file_sha256", "repository"}
    if (not isinstance(protocol, dict) or set(protocol) != fields
            or protocol["schema_version"] != "praxile.frozen_native_protocol.v1"):
        raise ValueError("invalid frozen native protocol")
    for key in ("development_task_ids", "heldout_task_ids"):
        values = protocol[key]
        if (not isinstance(values, list) or not 1 <= len(values) <= 10000
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("protocol requires explicit unique task lists")
    development, heldout = map(set, (protocol["development_task_ids"], protocol["heldout_task_ids"]))
    import re
    if (development & heldout or not isinstance(protocol["repository"], str) or not protocol["repository"].strip()
            or not isinstance(protocol["candidate_file_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", protocol["candidate_file_sha256"])):
        raise ValueError("invalid frozen protocol identity or overlapping splits")
    return development, heldout


def validate_protocol(protocol, task, candidate, file_sha256, repository):
    development, heldout = validate_protocol_shape(protocol)
    if task.split != "heldout" or task.task_id not in heldout:
        raise ValueError("task does not belong to disjoint held-out protocol")
    if protocol["candidate_file_sha256"] != file_sha256 or protocol["repository"] != repository:
        raise ValueError("candidate identity differs from frozen protocol")
    parsed = ContextCandidate.from_dict(candidate)
    if not parsed.source_task_ids or not set(parsed.source_task_ids) <= development:
        raise ValueError("candidate sources must be declared development tasks")
    parsed.validate_clean_track(heldout)
    return "sha256:" + hashlib.sha256(canonical_json(protocol).encode()).hexdigest()


def load_protocol(path, digest):
    # Reuse the bounded, duplicate-key and symlink rejecting JSON loader.
    return load_candidate(path, digest)
