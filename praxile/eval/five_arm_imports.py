"""Bind five-arm cells to local benchmark imports without copying private rubrics."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from ..utils import write_json
from .compare_imports import SCORES
from .content_review_files import _read
from .contracts import BenchmarkOutcome
from .five_arm_analysis import analyze_study
from .five_arm_plan import iter_schedule, plan_digest
from .v2.schema import EvalSchemaError


def analyze_imports(plan, bindings):
    schedule = {row["cell_id"]: row for row in iter_schedule(plan)}
    if not isinstance(bindings, list):
        raise EvalSchemaError("import bindings must be an array")
    records, checksums, used, seen = [], {}, set(), set()
    schema = None
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {
                "cell_id", "path", "sha256", "task_checksum", "maximum"}:
            raise EvalSchemaError("invalid import binding")
        cell = binding["cell_id"]
        if not isinstance(cell, str) or cell not in schedule or cell in seen:
            raise EvalSchemaError("unknown or duplicate cell")
        seen.add(cell)
        if not isinstance(binding["path"], str) or not binding["path"]:
            raise EvalSchemaError("import path required")
        path = Path(binding["path"])
        # The same bytes are hashed and parsed; never hash a second read of a mutable file.
        if path.is_symlink() or not path.is_file():
            raise EvalSchemaError("import must be a regular file")
        with path.open("rb") as handle:
            raw = handle.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise EvalSchemaError("import exceeds 16 MiB")
        digest = "sha256:" + hashlib.sha256(raw).hexdigest()
        if digest != binding["sha256"]:
            raise EvalSchemaError("import file digest mismatch")
        from ..structured_response import parse_submission_object
        report = parse_submission_object(raw.decode("utf-8"))
        version = report.get("schema_version")
        if not isinstance(version, str) or version not in SCORES or (schema is not None and schema != version):
            raise EvalSchemaError("unsupported or mixed benchmark import schemas")
        schema = version
        outcome = BenchmarkOutcome.from_dict(report.get("outcome"))
        task = schedule[cell]["task_id"]
        if outcome.task_id != task:
            raise EvalSchemaError("import task does not match scheduled task")
        checksum = binding["task_checksum"]
        if (not isinstance(checksum, str) or not checksum or report.get("task_checksum") != checksum
                or (task in checksums and checksums[task] != checksum)):
            raise EvalSchemaError("task checksum mismatch")
        checksums[task] = checksum
        if (task, digest) in used:
            raise EvalSchemaError("one task report cannot stand in for multiple cells")
        used.add((task, digest))
        maximum = binding["maximum"]
        if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0:
            raise EvalSchemaError("expected denominator must be positive and finite")
        score = next((score for score in outcome.scores if score.name == SCORES[version]), None)
        if outcome.status == "completed" and (score is None or score.maximum != maximum):
            raise EvalSchemaError("official primary score missing or denominator changed")
        records.append({"cell_id": cell, "plan_digest": plan_digest(plan), "source_sha256": digest,
                        "status": "completed" if outcome.status == "completed" else "unknown",
                        "score": score.value if score is not None else None, "maximum": maximum})
    result = analyze_study(plan, records)
    result.update(source_mode="local_benchmark_imports", source_schema=schema,
                  source_file_digests_verified=bool(bindings), task_checksums=checksums)
    result["limitations"][0] = (
        "Scores were parsed from digest-matched local import files; upstream verifier evidence and authenticity were not rechecked.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = _read(args.plan.parent, args.plan.name)
        bindings = json.loads(args.bindings.read_text())
        report = analyze_imports(plan, bindings)
        write_json(args.output, report)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Study import failed: {type(error).__name__}\n")
    print(json.dumps({"status": report["status"], "reported_cells": report["reported_cells"],
                      "promotion_eligible": False}))


if __name__ == "__main__":
    main()
