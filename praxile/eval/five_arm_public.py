"""Publishable, allowlisted development-study summary after offline audit."""
import argparse
import json
from pathlib import Path

from .five_arm_audit import audit_study
from .five_arm_plan import ARMS, iter_schedule
from .owned_content_review import _reader, _json
from .owned_native_cli import load_task


def public_summary(plan, tasks, *, directory, study_sha256, evaluator_root, public=False):
    if public is not True:
        raise ValueError("public summary requires explicit disclosure approval")
    audit = audit_study(plan, tasks, directory=directory, study_sha256=study_sha256,
                        evaluator_root=evaluator_root)
    reviews = {row["cell_id"]: row for row in audit["cell_reviews"]}
    task_numbers = {task: index + 1 for index, task in enumerate(plan["task_order"])}
    cells = []
    def status(row, key, allowed):
        raw = row.get(key) if row else None
        return raw if isinstance(raw, str) and raw in allowed else "unknown"
    for scheduled in iter_schedule(plan):
        row = reviews.get(scheduled["cell_id"])
        counts = row.get("verdict_counts") if row else None
        cells.append({"task_number": task_numbers[scheduled["task_id"]], "repetition": scheduled["repetition"],
            "arm": scheduled["arm"], "status": "not_recorded" if row is None else row["status"],
            "execution_status": status(row, "execution_status", {"dispatching", "executed_not_scored", "budget_exhausted", "outcome_unknown"}),
            "review_status": status(row, "review_status", {"reviewed", "abstained", "not_selected", "dispatching", "outcome_unknown", "not_recorded"}),
            "verdict_counts": counts})
    comparisons = {}
    for arm in ARMS[1:]:
        quality = audit["comparisons"][arm]
        tokens = audit["broker_cost_comparison"]["comparisons"][arm]
        comparisons[arm] = {"quality_complete_tasks": quality["sample_count"],
            "descriptive_label_delta": quality["descriptive_delta"],
            "label_familywise_ci95": quality["familywise_ci95"],
            "token_complete_tasks": tokens["sample_count"], "token_delta": tokens["token_delta"]}
    return {"schema_version": "praxile.public_five_arm_summary.v1", "track": plan["track"],
        "source_study_sha256": study_sha256, "plan_digest": audit["plan_digest"],
        "task_count": audit["task_count"], "complete_label_tasks": audit["complete_task_count"],
        "cells": cells, "comparisons": comparisons,
        "known_tokens": audit["broker_usage"]["known_tokens"], "total_tokens": audit["broker_usage"]["total_tokens"],
        "cost_usd": None, "official_evaluation": False, "promotion_eligible": False,
        "limitations": ["LLM requirement labels, not official benchmark scores or independently verified quality.",
            "Tasks are numbered in frozen order; task names, model names, paths, prompts and raw evidence are omitted.",
            "Unknown or unrecorded labels are not failures or zeros; quality and token cohorts can differ.",
            "Token costs include bound executor attempts and judge only; unknown totals are not zero cost.",
            "No monetary billing verification, causal benefit, remote provenance or promotion claim."]}


def render_markdown(report):
    def value(item):
        return "unknown" if item is None else str(item)
    lines = ["# Five-Arm " + ("Frozen Held-Out" if report["track"] == "heldout_frozen" else "Development") + " Result", "", "Not an official benchmark score or promotion decision.", "",
        f"Complete label tasks: {report['complete_label_tasks']}/{report['task_count']}",
        f"Study SHA-256: `{report['source_study_sha256']}`", "",
        "Broker tokens (input/output): known " + str(report["known_tokens"]["input"]) + "/" + str(report["known_tokens"]["output"])
        + "; complete totals " + value(report["total_tokens"]["input"]) + "/" + value(report["total_tokens"]["output"]) + ". Monetary cost: unknown.", "",
        "| Task | Repeat | Arm | Execution | Review | Preserved | Missing | Contradicted | Uncertain |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for cell in report["cells"]:
        counts = cell["verdict_counts"] or {}
        lines.append("| " + " | ".join([str(cell["task_number"]), str(cell["repetition"]), cell["arm"], cell["execution_status"], cell["review_status"]]
            + [value(counts.get(key)) for key in ("preserved", "missing", "contradicted", "uncertain")]) + " |")
    lines += ["", "## Paired Descriptive Comparisons", "",
        "Deltas are arm minus frozen executor. Repetitions are averaged within tasks.", "",
        "| Arm | Label tasks | Label delta | Token tasks | Input delta | Output delta |",
        "| --- | --- | --- | --- | --- | --- |"]
    for arm, item in report["comparisons"].items():
        lines.append("| " + " | ".join([arm, str(item["quality_complete_tasks"]), value(item["descriptive_label_delta"]),
            str(item["token_complete_tasks"]), value(item["token_delta"]["input"]), value(item["token_delta"]["output"])]) + " |")
    lines += ["", "## Limits", ""] + ["- " + item for item in report["limitations"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--study-sha256", required=True)
    parser.add_argument("--task", type=Path, action="append", required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    parser.add_argument("--public", action="store_true", help="Approve disclosure of aggregate results and evidence digests")
    parser.add_argument("--json", action="store_true", help="Print the allowlisted JSON instead of Markdown")
    args = parser.parse_args(argv)
    try:
        if not args.public:
            raise ValueError("public opt-in required")
        tasks = {}
        for path in args.task:
            task = load_task(path)
            if task.task_id in tasks:
                raise ValueError("duplicate task")
            tasks[task.task_id] = task
        plan = _json(_reader(args.study)("plan.json"))
        report = public_summary(plan, tasks, directory=args.study, study_sha256=args.study_sha256,
                                evaluator_root=args.evaluator_root, public=True)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Public study summary rejected: {type(error).__name__}\n")
    print(json.dumps(report, indent=2) if args.json else render_markdown(report), end="\n" if args.json else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
