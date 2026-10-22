from copy import deepcopy

import pytest

from praxile.eval.five_arm_conditions import (
    executor_conditions, validate_conditions, check_cell_conditions, check_attempt_conditions,
    check_cell_mechanisms, check_attempt_mechanisms,
)
from praxile.eval.five_arm_plan import ARMS
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


@pytest.mark.parametrize("arm", ARMS)
@pytest.mark.parametrize("defect", [None, "memory", "missing_memory", "numeric_memory", "candidate", "cell_candidate"])
def test_each_arm_has_only_its_scheduled_mechanisms(tmp_path, arm, defect):
    _, scheduled, _, _ = setup(tmp_path, arm)
    identity = {"candidate_digest": "sha256:" + "a" * 64}
    cell = {"cell": scheduled, "candidate_identity": identity if scheduled["experience_enabled"] else None}
    report = {"task_local_memory_enabled": scheduled["task_local_enabled"]}
    if scheduled["experience_enabled"]:
        report["development_context_identity"] = identity
    if defect == "memory":
        report["task_local_memory_enabled"] = not scheduled["task_local_enabled"]
    elif defect == "missing_memory":
        report.pop("task_local_memory_enabled")
    elif defect == "numeric_memory":
        report["task_local_memory_enabled"] = int(scheduled["task_local_enabled"])
    elif defect == "candidate":
        report["development_context_identity"] = {"candidate_digest": "sha256:" + "b" * 64}
    elif defect == "cell_candidate":
        cell["candidate_identity"] = {"candidate_digest": "sha256:" + "b" * 64}
    if defect is None:
        check_cell_mechanisms(cell, identity)
        check_attempt_mechanisms(report, cell)
    else:
        with pytest.raises(ValueError):
            check_cell_mechanisms(cell, identity)
            check_attempt_mechanisms(report, cell)


def test_cell_cannot_replace_boolean_switches_with_numbers(tmp_path):
    _, scheduled, _, _ = setup(tmp_path, "frozen_executor")
    scheduled["experience_enabled"] = 0
    with pytest.raises(ValueError):
        check_cell_mechanisms({"cell": scheduled, "candidate_identity": None}, {"candidate_digest": "fixture"})
