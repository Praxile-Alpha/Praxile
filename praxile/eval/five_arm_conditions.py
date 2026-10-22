"""Local executor-condition consistency, not remote model attestation."""
import hashlib
import re

from ..model_options import validate_request_options
from .v2.schema import canonical_json


def executor_conditions(options):
    return validate_conditions({"model": options["model"], "wheel_sha256": options["wheel_sha256"],
        "endpoint_sha256": hashlib.sha256(options["base_url"].encode()).hexdigest(),
        "model_request_options": options.get("model_request_options", {}),
        "max_steps": options.get("max_steps", 50)})


def validate_conditions(value):
    if (not isinstance(value, dict) or set(value) != {"model", "wheel_sha256", "endpoint_sha256",
            "model_request_options", "max_steps"} or not isinstance(value["model"], str)
            or not value["model"].strip() or type(value["max_steps"]) is not int
            or not 1 <= value["max_steps"] <= 1000
            or any(not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key])
                   for key in ("wheel_sha256", "endpoint_sha256"))):
        raise ValueError("missing or invalid study executor conditions")
    return {**value, "model_request_options": validate_request_options(value["model_request_options"])}


def check_cell_conditions(cell, conditions, task):
    expected = validate_conditions(conditions)
    executor = cell.get("executor")
    if not isinstance(executor, dict):
        raise ValueError("cell executor conditions missing")
    actual = {key: cell.get(key) if key == "max_steps" else executor.get(key) for key in expected}
    if (canonical_json(actual) != canonical_json(expected)
            or executor.get("image") != task.environment.revision
            or cell.get("task_fingerprint") != task.fingerprint):
        raise ValueError("cell differs from study executor conditions")


def check_attempt_conditions(report, conditions, task):
    expected = validate_conditions(conditions)
    actual = {key: report.get(key) for key in expected}
    if (canonical_json(actual) != canonical_json(expected)
            or report.get("schema_version") != "praxile.owned_native_execution.v1"
            or report.get("task_id") != task.task_id or report.get("task_fingerprint") != task.fingerprint
            or report.get("image") != task.environment.revision):
        raise ValueError("attempt differs from study executor conditions")
