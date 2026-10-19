"""Descriptive task-paired comparison of official benchmark imports."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .contracts import BenchmarkOutcome
from .v2.schema import EvalSchemaError
from ..utils import write_json


SCORES = {"praxile.jobbench_import.v1": "official_weighted_score",
          "praxile.workbuddy_import.v1": "official_composite_reward"}


def compare_imports(repetitions: list[dict]) -> dict:
    if not isinstance(repetitions, list) or not repetitions:
        raise EvalSchemaError("at least one explicit repetition is required")
    groups = {}
    schema = None
    task_checksums = {}
    maxima = {}
    for index, repetition in enumerate(repetitions, 1):
        if not isinstance(repetition, dict):
            raise EvalSchemaError("repetition must be an object")
        arms = {}
        for arm in ("baseline", "candidate"):
            records = repetition.get(arm)
            if not isinstance(records, list) or not records:
                raise EvalSchemaError("each repetition needs nonempty baseline and candidate imports")
            tasks = {}
            for record in records:
                if not isinstance(record, dict):
                    raise EvalSchemaError("import report must be an object")
                version = record.get("schema_version")
                if version not in SCORES or (schema is not None and version != schema):
                    raise EvalSchemaError("do not mix benchmark import schemas")
                schema = version
                outcome = BenchmarkOutcome.from_dict(record.get("outcome"))
                if outcome.task_id in tasks:
                    raise EvalSchemaError("duplicate task in one arm")
                checksum = record.get("task_checksum")
                if not isinstance(checksum, str) or not checksum:
                    raise EvalSchemaError("frozen task checksum missing")
                if outcome.task_id in task_checksums and task_checksums[outcome.task_id] != checksum:
                    raise EvalSchemaError("task checksum changed between arms or repetitions")
                task_checksums[outcome.task_id] = checksum
                score = next((item for item in outcome.scores if item.name == SCORES[schema]), None)
                if outcome.status == "completed" and score is None:
                    raise EvalSchemaError("completed official import lacks primary score")
                if score is not None:
                    if outcome.task_id in maxima and maxima[outcome.task_id] != score.maximum:
                        raise EvalSchemaError("official denominator changed for the same task")
                    maxima[outcome.task_id] = score.maximum
                tasks[outcome.task_id] = {"status": outcome.status,
                    "value": score.value if score else None, "maximum": score.maximum if score else None,
                    "normalized": score.value / score.maximum if score else None,
                    "execution_status": record.get("execution_status", "unknown")}
            arms[arm] = tasks
        for task_id in sorted(set(arms["baseline"]) | set(arms["candidate"])):
            groups.setdefault(task_id, []).append({"repetition": index,
                "baseline": arms["baseline"].get(task_id), "candidate": arms["candidate"].get(task_id)})
    task_rows = []
    deltas = []
    for task_id, pairs in sorted(groups.items()):
        complete = len(pairs) == len(repetitions) and all(pair[arm] is not None
            and pair[arm]["normalized"] is not None for pair in pairs for arm in ("baseline", "candidate"))
        delta = (sum(pair["candidate"]["normalized"] - pair["baseline"]["normalized"]
                     for pair in pairs) / len(pairs)) if complete else None
        if delta is not None:
            deltas.append(delta)
        task_rows.append({"task_id": task_id, "task_checksum": task_checksums[task_id],
            "complete": complete, "mean_normalized_delta": delta, "pairs": pairs})
    estimate = sum(deltas) / len(deltas) if deltas else None
    margin = 2 * math.sqrt(math.log(40) / (2 * len(deltas))) if deltas else None
    return {"schema_version": "praxile.imported_benchmark_comparison.v1", "source_schema": schema,
        "statistical_unit": "task", "repetition_count": len(repetitions), "sample_count": len(deltas),
        "incomplete_task_count": len(task_rows) - len(deltas), "tasks": task_rows,
        "delta": {"estimate": estimate, "ci95": [max(-1, estimate - margin), min(1, estimate + margin)]
                  if deltas else [-1.0, 1.0], "method": "two_sided_hoeffding_task_means"},
        "controlled_experiment_verified": False, "promotion_eligible": False,
        "limitations": ["Caller must independently verify frozen model, executor, environment and judge identities.",
                        "Intervals assume independent task clusters; repetitions within a task are not independent samples.",
                        "Complete-pair estimates may be selection-biased when tasks are missing; inspect every incomplete task.",
                        "Imported report hashes establish file identity, not authenticity or causal effect."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest_raw = args.manifest.read_bytes()
    plan = json.loads(manifest_raw)
    if not isinstance(plan, dict) or plan.get("schema_version") != "praxile.import_comparison_plan.v1":
        parser.error("expected praxile.import_comparison_plan.v1")
    if not isinstance(plan.get("repetitions"), list) or not plan["repetitions"]:
        parser.error("repetitions must be a nonempty array")
    repetitions = []
    inputs = []
    for repetition in plan.get("repetitions", []):
        if not isinstance(repetition, dict):
            parser.error("repetition must be an object")
        arms = {}
        for arm in ("baseline", "candidate"):
            arms[arm] = []
            paths = repetition.get(arm)
            if not isinstance(paths, list) or not paths:
                parser.error("each arm must list imported report paths")
            for value in paths:
                if not isinstance(value, str) or not value:
                    parser.error("report paths must be nonempty strings")
                path = (args.manifest.parent / value).resolve(strict=True)
                if path == args.output.resolve() or args.output.resolve() == args.manifest.resolve():
                    parser.error("output must not overwrite an input")
                raw = path.read_bytes()
                arms[arm].append(json.loads(raw))
                inputs.append({"repetition": len(repetitions) + 1, "arm": arm,
                               "digest": "sha256:" + hashlib.sha256(raw).hexdigest()})
        repetitions.append(arms)
    try:
        report = compare_imports(repetitions)
    except EvalSchemaError as exc:
        parser.error(str(exc))
    report["inputs"] = inputs
    report["plan_digest"] = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
    write_json(args.output, report)
    print(f"Compared {report['sample_count']} complete task pairs; {report['incomplete_task_count']} incomplete tasks.")


if __name__ == "__main__":
    main()
