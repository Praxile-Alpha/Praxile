"""Freeze a five-arm study before execution; never launches or scores a task."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from ..utils import file_lock
from .v2.experiment import _write_once
from .v2.schema import EvalSchemaError, canonical_json


ARMS = ("frozen_executor", "budget_matched_repeat", "experience_only",
        "task_local_only", "full_praxile")
IDENTITIES = ("dataset", "executor", "model", "environment", "judge", "memory_snapshot",
              "experience_policy", "task_local_policy", "terminal_selection")
SCHEMA = "praxile.five_arm_plan.v1"


def compile_plan(spec):
    """All identities are declared SHA-256 digests, not provenance attestations."""
    required = {"study_id", "task_ids", "heldout_task_ids", "repetitions", "repeat_attempts",
                "identities", "budget", "arm_order"}
    if not isinstance(spec, dict) or set(spec) != required:
        raise EvalSchemaError("five-arm plan requires exactly the documented fields")
    spec = deepcopy(spec)
    if not isinstance(spec["study_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}", spec["study_id"]):
        raise EvalSchemaError("unsafe study ID")
    for key in ("task_ids", "heldout_task_ids"):
        values = spec[key]
        if (not isinstance(values, list) or not 1 <= len(values) <= 10000
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in values)
                or len(set(values)) != len(values)):
            raise EvalSchemaError("task lists must contain unique nonempty IDs")
    if set(spec["task_ids"]) & set(spec["heldout_task_ids"]):
        raise EvalSchemaError("development and held-out task IDs overlap")
    for key, low, high in (("repetitions", 1, 100), ("repeat_attempts", 2, 100)):
        if type(spec[key]) is not int or not low <= spec[key] <= high:
            raise EvalSchemaError(f"invalid {key}")
    identities = spec["identities"]
    if (not isinstance(identities, dict) or set(identities) != set(IDENTITIES)
            or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
                   for value in identities.values())):
        raise EvalSchemaError("all frozen identities require sha256 digests")
    budget = spec["budget"]
    if (not isinstance(budget, dict) or not {"total_tokens", "model_calls", "wall_seconds"} <= set(budget)
            or set(budget) - {"total_tokens", "model_calls", "wall_seconds", "request_bytes"}
            or any(type(value) is not int or not 1 <= value <= 10**9 for value in budget.values())):
        raise EvalSchemaError("budget requires positive integer tokens, model calls and wall seconds")
    if budget["model_calls"] < spec["repeat_attempts"]:
        raise EvalSchemaError("repeat attempts exceed the shared model-call budget")
    order = spec["arm_order"]
    if (not isinstance(order, list) or len(order) != len(ARMS)
            or any(not isinstance(name, str) for name in order) or set(order) != set(ARMS)):
        raise EvalSchemaError("arm_order must contain each of the five arms exactly once")
    arms = {}
    for name in order:
        arms[name] = {"experience_enabled": name in {"experience_only", "full_praxile"},
                      "task_local_enabled": name in {"task_local_only", "full_praxile"},
                      "max_attempts": spec["repeat_attempts"] if name == "budget_matched_repeat" else 1,
                      "shared_budget": deepcopy(budget), "persistent_updates_allowed": False}
    return {"schema_version": SCHEMA, "spec": spec, "arms": arms,
            "track": "development", "status": "plan_only", "promotion_eligible": False,
            "budget_scope": "per_arm_task_repetition_shared_across_attempts_and_all_roles",
            "task_order": list(spec["task_ids"]), "schedule_order": "repetition_task_arm",
            "budget_enforcement_verified": False, "provenance_verified": False,
            "selection_uses_heldout_feedback": False,
            "limitations": ["Declared digests do not attest source identity or split membership.",
                            "Common budget ceilings do not establish equal observed resource use.",
                            "Execution, budget enforcement, selection and analysis are not implemented by this planner."]}


def plan_digest(plan):
    return "sha256:" + hashlib.sha256(canonical_json(plan).encode()).hexdigest()


def iter_schedule(plan):
    if not isinstance(plan, dict) or plan != compile_plan(plan.get("spec")):
        raise EvalSchemaError("plan differs from its compiled specification")
    spec = plan["spec"]
    for repetition in range(spec["repetitions"]):
        for task_index, task_id in enumerate(spec["task_ids"]):
            for arm in spec["arm_order"]:
                yield {"cell_id": f"{spec['study_id']}.r{repetition}.t{task_index}.{arm}",
                       "task_id": task_id, "repetition": repetition, "arm": arm,
                       **deepcopy(plan["arms"][arm])}


def freeze_plan(spec, directory):
    plan = compile_plan(spec)
    root = Path(directory)
    with file_lock(root / "plan"):
        _write_once(root / "manifest.json", plan)
    return {"manifest_path": str(root / "manifest.json"), "plan_digest": plan_digest(plan),
            "scheduled_cells": len(spec["task_ids"]) * spec["repetitions"] * len(ARMS),
            "status": "plan_only", "promotion_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.spec.open("rb") as handle:
            data = handle.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise EvalSchemaError("spec exceeds 1 MiB")
        from ..structured_response import parse_submission_object
        result = freeze_plan(parse_submission_object(data.decode("utf-8")), args.output)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Cannot freeze study plan: {type(error).__name__}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
