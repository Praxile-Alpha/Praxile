"""Offline artifact-bound analysis of development judge labels, never official scores."""
from pathlib import Path
import argparse
import json

from .owned_content_review import _reader, _json, _sha
from .five_arm_plan import iter_schedule, plan_digest
from .five_arm_analysis import analyze_study
from .five_arm_review import prepare_selected_cell_review
from .content_review_compare import _checked
from .content_review_batch import plan_content_review_batch
from .v2.schema import canonical_json
from .five_arm_usage import executor_receipts, summarize_receipts
from .five_arm_conditions import validate_conditions, check_cell_conditions


def audit_study(plan, tasks, *, directory, study_sha256, evaluator_root):
    """Caller supplies an expected study receipt digest and original frozen tasks."""
    schedule = list(iter_schedule(plan))
    if set(tasks) != set(plan["spec"]["task_ids"]):
        raise ValueError("audit requires exact frozen tasks")
    root = Path(directory)
    read = _reader(root)
    raw = read("study.json", max_bytes=16 * 1024**2)
    if _sha(raw) != study_sha256 or _json(read("plan.json")) != plan:
        raise ValueError("study receipt or plan changed")
    study = _json(raw)
    if (study.get("schema_version") != "praxile.five_arm_study_execution.v1"
            or study.get("plan_digest") != plan_digest(plan)
            or study.get("task_fingerprints") != {key: value.fingerprint for key, value in tasks.items()}):
        raise ValueError("study task identity mismatch")
    identity = study.get("judge_identity")
    conditions = validate_conditions(study.get("executor_conditions"))
    if (not isinstance(identity, dict) or "sha256:" + _sha(canonical_json(identity).encode())
            != plan["spec"]["identities"]["judge"]):
        raise ValueError("study lacks matching frozen judge identity")
    entries = study.get("cells")
    if not isinstance(entries, list) or len(entries) > len(schedule):
        raise ValueError("invalid study cells")
    records, rows, usage_receipts = [], [], []
    for index, entry in enumerate(entries):
        expected = schedule[index]
        if not isinstance(entry, dict) or entry.get("cell_id") != expected["cell_id"]:
            raise ValueError("study cell order mismatch")
        row = {"cell_id": expected["cell_id"], "status": "unknown", "verdict_counts": None}
        rows.append(row)
        review = entry.get("review", {})
        if not isinstance(review, dict):
            raise ValueError("invalid review receipt")
        if "report" in entry:
            if entry["report"] != f"cells/{index}/cell.json":
                raise ValueError("cost cell path mismatch")
            raw_cell = read(entry["report"])
            if _sha(raw_cell) != entry.get("sha256"):
                raise ValueError("cost cell receipt changed")
            cell = _json(raw_cell)
            if cell.get("cell") != expected or cell.get("plan_digest") != plan_digest(plan):
                raise ValueError("cost cell belongs to another plan")
            task = tasks[expected["task_id"]]
            check_cell_conditions(cell, conditions, task)
            usage_receipts.extend(executor_receipts(root / "cells" / str(index), cell,
                                                   conditions=conditions, task=task))
        else:
            usage_receipts.append(("executor", None))
        if review.get("status") not in {"reviewed", "abstained"}:
            if review.get("status") != "not_selected":
                usage_receipts.append(("judge", None))
            continue
        if entry.get("report") != f"cells/{index}/cell.json" or review.get("report") != f"reviews/{index}/report.json":
            raise ValueError("study evidence path mismatch")
        cell_raw = read(entry["report"])
        if _sha(cell_raw) != entry.get("sha256"):
            raise ValueError("cell receipt changed")
        task_id = expected["task_id"]
        settings = identity["artifacts"][task_id]
        inputs, binding, selection = prepare_selected_cell_review(plan, expected["cell_id"], tasks[task_id],
            cell_directory=root / "cells" / str(index), cell_sha256=entry["sha256"], evaluator_root=evaluator_root,
            **{key: value for key, value in settings.items() if key != "evaluator_sha256"})
        evidence = review.get("evidence_sha256")
        names = {"manifest.json", "trajectory.json", "broker-usage.json", "inputs.json"}
        if not isinstance(evidence, dict) or set(evidence) != names:
            raise ValueError("review evidence hashes missing")
        files = {}
        for name, digest in {**evidence, "report.json": review["sha256"]}.items():
            data = read(f"reviews/{index}/{name}", max_bytes=16 * 1024**2)
            if _sha(data) != digest:
                raise ValueError("review evidence changed")
            files[name] = _json(data)
        usage_receipts.append(("judge", files["broker-usage.json"]))
        manifest, report, trajectory = (files[name] for name in ("manifest.json", "report.json", "trajectory.json"))
        if (manifest.get("schema_version") != "praxile.broker_content_review.v1"
                or files["inputs.json"] != inputs or manifest.get("execution_binding") != binding
                or manifest.get("selection_binding") != selection
                or any(manifest.get(key) != identity.get(key) for key in ("model", "endpoint_sha256", "request_options"))):
            raise ValueError("review is not bound to selected artifacts and judge")
        batch_plan = plan_content_review_batch(**inputs, total_output_tokens=identity["total_output_tokens"],
            batch_size=identity["batch_size"], timeout_per_call=identity["timeout_per_call"],
            private=False, response_mode="function", citation_mode="units")
        checked_plan, verdicts = _checked(report)
        if (checked_plan != batch_plan or manifest.get("plan") != batch_plan
                or trajectory.get("content_review_batches") != [report]
                or trajectory.get("content_reviews") != report.get("groups")
                or report.get("status") != review["status"]):
            raise ValueError("review plan or trajectory mismatch")
        counts = None if verdicts is None else {label: list(verdicts.values()).count(label)
            for label in ("preserved", "missing", "contradicted", "uncertain")}
        complete = counts is not None and counts["uncertain"] == 0
        row.update(status="complete_judge_labels" if complete else "unknown", verdict_counts=counts)
        records.append({"cell_id": expected["cell_id"], "plan_digest": plan_digest(plan),
            "source_sha256": "sha256:" + review["sha256"], "status": "completed" if complete else "unknown",
            "score": counts["preserved"] if complete else None, "maximum": len(inputs["requirements"])})
    analysis = analyze_study(plan, records)
    analysis.update(metric="llm_declared_requirement_preservation", official_evaluation=False,
        local_file_consistency_checked=True, semantic_accuracy_verified=False, source_study_sha256=study_sha256,
        executor_conditions=conditions, recorded_executor_conditions_checked=True,
        cell_reviews=rows, checked_review_cells=len(records),
        all_scheduled_review_evidence_checked=len(records) == len(schedule))
    analysis["broker_usage"] = summarize_receipts(usage_receipts)
    analysis["broker_usage"]["all_scheduled_cells_recorded"] = len(entries) == len(schedule)
    analysis["limitations"].append("Local files and selected artifacts were checked; labels are uncalibrated LLM judgments, not task success or provenance attestation.")
    return analysis


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--study-sha256", required=True)
    parser.add_argument("--task", type=Path, action="append", required=True)
    parser.add_argument("--evaluator-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .owned_native_cli import load_task
        tasks = {}
        for path in args.task:
            task = load_task(path)
            if task.task_id in tasks:
                raise ValueError("duplicate task identity")
            tasks[task.task_id] = task
        plan = _json(_reader(args.study)("plan.json"))
        result = audit_study(plan, tasks, directory=args.study, study_sha256=args.study_sha256,
                             evaluator_root=args.evaluator_root)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Study audit rejected: {type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete_task_count"] == result["task_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
