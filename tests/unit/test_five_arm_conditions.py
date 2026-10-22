from copy import deepcopy

import pytest

from praxile.eval.five_arm_conditions import (
    executor_conditions, validate_conditions, check_cell_conditions, check_attempt_conditions,
)
from test_five_arm_execution import setup


@pytest.mark.parametrize("field,value", [("model", "other"), ("wheel_sha256", "b" * 64),
    ("endpoint_sha256", "c" * 64), ("max_steps", 1), ("max_steps", True),
    ("model_request_options", {"reasoning_effort": "high"}), ("model_request_options", None)])
def test_receipts_must_agree_on_all_executor_conditions(tmp_path, field, value):
    _, _, task, options = setup(tmp_path, "frozen_executor")
    expected = executor_conditions(options)
    report = {**expected, "schema_version": "praxile.owned_native_execution.v1",
              "task_id": task.task_id, "task_fingerprint": task.fingerprint, "image": task.environment.revision}
    cell = {"executor": {key: val for key, val in report.items() if key in expected and key != "max_steps"},
            "max_steps": expected["max_steps"], "task_fingerprint": task.fingerprint}
    cell["executor"]["image"] = task.environment.revision
    check_cell_conditions(cell, expected, task)
    check_attempt_conditions(report, expected, task)
    report[field] = value
    with pytest.raises(ValueError, match="attempt differs"):
        check_attempt_conditions(report, expected, task)
    if field == "max_steps":
        cell[field] = value
    else:
        cell["executor"][field] = value
    with pytest.raises(ValueError, match="cell differs"):
        check_cell_conditions(cell, expected, task)


@pytest.mark.parametrize("defect", ["missing", "extra", "bad_sha", "bool_steps"])
def test_old_or_invalid_study_identity_is_not_inferred(tmp_path, defect):
    _, _, _, options = setup(tmp_path, "frozen_executor")
    expected = executor_conditions(options)
    value = deepcopy(expected)
    if defect == "missing":
        value.pop("model_request_options")
    elif defect == "extra":
        value["api_key"] = "PRIVATE"
    elif defect == "bad_sha":
        value["wheel_sha256"] = "invalid"
    else:
        value["max_steps"] = True
    with pytest.raises(ValueError):
        validate_conditions(value)
