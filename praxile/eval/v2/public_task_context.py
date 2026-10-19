"""Explicit bounded public requirements, never implicitly loaded from evaluators."""
from copy import deepcopy
import hashlib
import re

from ...services.native_trace_import import _read
from .schema import EvalSchemaError


def validate_public_task_context(value, development_task_ids, heldout_task_ids):
    if (not isinstance(value, dict) or set(value) != {
            "schema_version", "visibility", "task_id", "goal", "constraints"}
            or value["schema_version"] != "praxile.public_task_context.v1"
            or value["visibility"] != "public_task"
            or not isinstance(value["task_id"], str)
            or value["task_id"] not in development_task_ids
            or value["task_id"] in heldout_task_ids):
        raise EvalSchemaError("public task context requires exact schema and development membership")
    if not isinstance(value["goal"], str) or not value["goal"].strip() or len(value["goal"]) > 4000:
        raise EvalSchemaError("public task goal must be nonempty and at most 4000 characters")
    constraints = value["constraints"]
    if (not isinstance(constraints, list) or not 1 <= len(constraints) <= 20
            or any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in constraints)):
        raise EvalSchemaError("public task constraints require 1..20 nonempty strings of at most 1000 characters")
    return deepcopy(value)


def load_public_task_context(path, expected_sha256, task_id, heldout_task_ids):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise EvalSchemaError("public task context requires explicit SHA256")
    raw, value = _read(path)
    if len(raw) > 65536:
        raise EvalSchemaError("public task context exceeds 64 KiB")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise EvalSchemaError("public task context digest mismatch")
    return validate_public_task_context(value, [task_id], heldout_task_ids)
