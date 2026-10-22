"""Descriptive task-paired broker tokens; no unsupported cost intervals."""
import math

from .five_arm_plan import ARMS, iter_schedule, plan_digest


def compare_usage(plan, cell_usage):
    """Consume audited per-cell summaries, retaining only a shared complete cohort.

    Values include all bound attempts and judge calls, not only selected outputs.
    This function does not independently authenticate its caller's summaries.
    """
    schedule = list(iter_schedule(plan))
    known = {row["cell_id"] for row in schedule}
    if not isinstance(cell_usage, dict) or set(cell_usage) - known:
        raise ValueError("usage contains undeclared study cells")
    cells = {}
    for cell_id, item in cell_usage.items():
        values = item.get("total_tokens") if isinstance(item, dict) else None
        if not isinstance(values, dict) or set(values) != {"input", "output"}:
            raise ValueError("invalid cell token totals")
        if any(value is not None and (type(value) is not int or not 0 <= value <= 2**53)
               for value in values.values()):
            raise ValueError("invalid cell token count")
        cells[cell_id] = values
    by_task = {task: [] for task in plan["task_order"]}
    for cell in schedule:
        by_task[cell["task_id"]].append(cell)
    tasks, differences = [], {arm: {side: [] for side in ("input", "output")} for arm in ARMS[1:]}
    for task_id, rows in by_task.items():
        missing = [row["cell_id"] for row in rows if row["cell_id"] not in cells]
        unknown = [row["cell_id"] for row in rows if row["cell_id"] in cells
                   and any(value is None for value in cells[row["cell_id"]].values())]
        means = {}
        if not missing and not unknown:
            for arm in ARMS:
                entries = [cells[row["cell_id"]] for row in rows if row["arm"] == arm]
                means[arm] = {side: math.fsum(item[side] for item in entries) / len(entries)
                              for side in ("input", "output")}
            for arm in differences:
                for side in ("input", "output"):
                    differences[arm][side].append(means[arm][side] - means["frozen_executor"][side])
        tasks.append({"task_id": task_id, "complete": not missing and not unknown,
                      "missing_cells": missing, "unknown_cells": unknown, "arm_means": means})
    comparisons = {}
    for arm, sides in differences.items():
        comparisons[arm] = {"baseline": "frozen_executor", "sample_count": len(sides["input"]),
            "token_delta": {side: math.fsum(values) / len(values) if values else None
                            for side, values in sides.items()}, "confidence_interval": None}
    return {"schema_version": "praxile.five_arm_cost_comparison.v1", "plan_digest": plan_digest(plan),
        "statistical_unit": "task", "task_count": len(tasks),
        "complete_task_count": sum(task["complete"] for task in tasks), "tasks": tasks,
        "comparisons": comparisons, "delta_direction": "arm_minus_frozen_executor",
        "scope": "bound_executor_attempts_and_judge_broker_receipts", "cost_usd": None,
        "promotion_eligible": False,
        "limitations": ["A common complete-task cohort is required across both token fields and all five arms; missingness may bias descriptive deltas.",
            "Task repetitions are averaged before task-level pairing; no weighting by task token count.",
            "No precommitted verified bound on observed input tokens: no confidence interval or significance claim.",
            "No upstream experience-design costs, billing verification, or quality-adjusted efficiency claim."]}
