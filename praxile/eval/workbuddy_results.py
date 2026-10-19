"""Import private WorkBuddy CompositeVerifier evidence, not self-judgments."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from .contracts import BenchmarkOutcome, BenchmarkScore
from .native_trial_binding import bind_native_trace
from .v2.schema import EvalSchemaError


def import_trial(task_id: str, trial_dir: Path, *, expected_task_checksum: str) -> dict:
    root = trial_dir.resolve(strict=True)
    evidence = {}

    def read(name):
        path = root / name
        if any(part.is_symlink() for part in (path, *path.parents)
               if part != root and part.is_relative_to(root)):
            raise EvalSchemaError("trial evidence must not contain symlinks")
        raw = path.read_bytes()
        evidence[name] = "sha256:" + hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise EvalSchemaError("official evidence must be an object")
        return value

    trial = read("result.json")
    if (not expected_task_checksum or trial.get("task_name") != "workbuddy/" + task_id
            or trial.get("task_checksum") != expected_task_checksum):
        raise EvalSchemaError("trial task identity or frozen checksum mismatch")
    exception = trial.get("exception_info") or {}
    execution = "unknown"
    if exception.get("exception_type") == "AgentTimeoutError":
        execution = "timed_out"
    elif (trial.get("agent_execution") or {}).get("finished_at"):
        execution = "finished"
    report = {"schema_version": "praxile.workbuddy_import.v1", "task_checksum": expected_task_checksum,
              "execution_status": execution, "executor_exception": exception or None,
              "usage": trial.get("agent_result"), "official_details": None,
              "verifier_environment_mode": trial.get("verifier_environment_mode", "unknown")}
    scores = ()
    status = "timed_out" if exception.get("exception_type") == "VerifierTimeoutError" else "infra_error"
    score_path = root / "verifier/score.json"
    if score_path.exists() or score_path.is_symlink():
        details = read("verifier/score.json")
        reward = read("verifier/reward.json")
        reported = (trial.get("verifier_result") or {}).get("rewards")
        if not isinstance(reported, dict) or not trial.get("finished_at"):
            raise EvalSchemaError("official scoring requires a finished trial and verifier result")
        if set(reported) != set(reward):
            raise EvalSchemaError("official reward keys disagree")
        verifier = (trial.get("config") or {}).get("verifier") or {}
        if verifier.get("import_path") != "workbuddy_bench.judge:CompositeVerifier":
            raise EvalSchemaError("trial did not select official CompositeVerifier")
        for key, value in reward.items():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise EvalSchemaError("official numeric results must be finite")
            for source in (details, reported):
                other = source.get(key)
                if type(other) not in (int, float) or not math.isfinite(other) or not math.isclose(value, other, rel_tol=0, abs_tol=1e-9):
                    raise EvalSchemaError("official reward files disagree")
        value = reward.get("reward")
        if type(value) not in (int, float) or not 0 <= value <= 1:
            raise EvalSchemaError("official reward must lie in [0, 1]")
        for field in ("tests_passed", "tests_total"):
            if field in reward and (type(reward[field]) is not int or reward[field] < 0):
                raise EvalSchemaError("official test counts must be nonnegative integers")
        if "tests_passed" in reward and "tests_total" in reward and reward["tests_passed"] > reward["tests_total"]:
            raise EvalSchemaError("official passed count exceeds total")
        for field in ("judges", "verdicts"):
            if not isinstance(details.get(field), list) or any(not isinstance(item, dict) for item in details[field]):
                raise EvalSchemaError("official judge details are missing")
        test_status = details.get("test_status")
        if test_status in {"judge_error", "build_error"} and value != 0:
            raise EvalSchemaError("fatal official scoring errors must have zero reward")
        if test_status == "judge_error":
            status = "judge_unavailable"
        elif test_status == "build_error":
            status = "infra_error"
        elif test_status in {"full_pass", "partial_pass", "no_pass"}:
            expected = "full_pass" if value == 1 else "partial_pass" if value > 0 else "no_pass"
            if test_status != expected:
                raise EvalSchemaError("official test status disagrees with reward")
            status = "completed"
            scores = (BenchmarkScore("official_composite_reward", value, 1),)
        else:
            raise EvalSchemaError("unknown official test status")
        report["official_details"] = details
        # Preserve non-fatal judge errors; never infer model identity or enablement.
        report["judge_issues"] = [item for item in details["judges"]
                                  if item.get("status") in {"judge_error", "build_error", "abstain", "review"}]
    report["native_trace_binding"] = bind_native_trace(trial, read, evidence)
    from .native_artifacts import audit_output
    report["output_retention"] = audit_output(root, trial)
    report["outcome"] = BenchmarkOutcome(task_id, status, scores, tuple(evidence)).to_dict()
    report["evidence_digests"] = evidence
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dirhash import dirhash
    from .workbuddy import WorkBuddyDataset
    dataset = WorkBuddyDataset(args.dataset, revision=args.revision, expected_digest=args.inventory_sha256)
    task, = dataset.select((args.task_id,))
    report = import_trial(task.task_id, args.trial,
                          expected_task_checksum=dirhash(dataset.root / "tasks" / task.task_id, "sha256"))
    report.update(dataset_revision=dataset.revision, inventory_sha256=dataset.digest, task_fingerprint=task.fingerprint)
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"Imported {task.task_id}: {report['outcome']['status']}")


if __name__ == "__main__":
    main()
