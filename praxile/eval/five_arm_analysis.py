"""Task-cluster analysis of declared five-arm results, without promotion."""
import argparse
import json
import math
from pathlib import Path
import re

from ..utils import write_json
from .five_arm_plan import ARMS, iter_schedule, plan_digest
from .v2.schema import EvalSchemaError


def analyze_study(plan, records):
    schedule = {row["cell_id"]: row for row in iter_schedule(plan)}
    task_cells = {task: [] for task in plan["task_order"]}
    for cell, row in schedule.items():
        task_cells[row["task_id"]].append(cell)
    digest = plan_digest(plan)
    if not isinstance(records, list):
        raise EvalSchemaError("cell results must be an array")
    indexed, maxima = {}, {}
    fields = {"cell_id", "plan_digest", "source_sha256", "status", "score", "maximum"}
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise EvalSchemaError("invalid cell result fields")
        cell = record["cell_id"]
        if not isinstance(cell, str) or cell not in schedule or cell in indexed:
            raise EvalSchemaError("unknown or duplicate scheduled cell")
        if record["plan_digest"] != digest:
            raise EvalSchemaError("cell result belongs to a different plan")
        if not isinstance(record["source_sha256"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", record["source_sha256"]):
            raise EvalSchemaError("cell requires a source report digest")
        status = record["status"]
        if status not in ("completed", "failed", "unknown"):
            raise EvalSchemaError("invalid cell status")
        maximum, score = record["maximum"], record["score"]
        if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0:
            raise EvalSchemaError("invalid score denominator")
        if status == "completed":
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= maximum:
                raise EvalSchemaError("invalid completed score")
        elif score is not None:
            raise EvalSchemaError("unfinished cells must not invent a score")
        task = schedule[cell]["task_id"]
        if task in maxima and maxima[task] != maximum:
            raise EvalSchemaError("task denominator differs between arms or repetitions")
        maxima[task] = maximum
        indexed[cell] = {**record, "normalized_score": score / maximum if score is not None else None}
    task_rows, deltas = [], {arm: [] for arm in ARMS[1:]}
    for task in plan["task_order"]:
        cells = task_cells[task]
        missing = [cell for cell in cells if cell not in indexed]
        unfinished = [cell for cell in cells if cell in indexed and indexed[cell]["status"] != "completed"]
        means = {}
        complete = not missing and not unfinished
        if complete:
            for arm in ARMS:
                values = [indexed[cell]["normalized_score"] for cell in cells if schedule[cell]["arm"] == arm]
                means[arm] = sum(values) / len(values)
            for arm in deltas:
                deltas[arm].append(means[arm] - means["frozen_executor"])
        task_rows.append({"task_id": task, "complete": complete, "missing_cells": missing,
                          "unfinished_cells": unfinished, "arm_means": means})
    complete_tasks = sum(row["complete"] for row in task_rows)
    complete_study = complete_tasks == len(task_rows)
    comparisons = {}
    for arm, values in deltas.items():
        estimate = sum(values) / len(values) if values else None
        # Four predeclared comparisons: Bonferroni family-wise 95% Hoeffding intervals.
        margin = 2 * math.sqrt(math.log(2 * len(deltas) / 0.05) / (2 * len(values))) if values else None
        comparisons[arm] = {"baseline": "frozen_executor", "sample_count": len(values),
            "descriptive_delta": estimate,
            "familywise_ci95": [max(-1, estimate - margin), min(1, estimate + margin)]
                if complete_study else None}
    return {"schema_version": "praxile.five_arm_analysis.v1", "plan_digest": digest,
        "track": plan["track"],
        "status": "complete_declared_results" if complete_study else "incomplete",
        "statistical_unit": "task", "scheduled_cells": len(schedule), "reported_cells": len(indexed),
        "complete_task_count": complete_tasks, "task_count": len(task_rows), "tasks": task_rows,
        "comparisons": comparisons, "source_report_digests": {cell: row["source_sha256"] for cell, row in indexed.items()},
        "interval_method": "bonferroni_two_sided_hoeffding_task_means",
        "controlled_execution_verified": False, "budget_enforcement_verified": False,
        "promotion_eligible": False,
        "limitations": ["Scores and source hashes are caller declarations, not verified source reports.",
            "Task clusters must be independent; repeated runs within a task are not independent samples.",
            "All five arms share the complete-task cohort; missing tasks suppress intervals and may bias descriptive means.",
            "This does not verify runtime mechanisms, selection, budgets, provenance or held-out transfer."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
        results = json.loads(args.results.read_text())
        report = analyze_study(plan, results)
        write_json(args.output, report)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Study analysis failed: {type(error).__name__}\n")
    print(json.dumps({"status": report["status"], "complete_task_count": report["complete_task_count"],
                      "promotion_eligible": False}))


if __name__ == "__main__":
    main()
