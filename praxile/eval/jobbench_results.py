"""Normalize official JobBench evidence without using Praxile self-rewards."""
from __future__ import annotations

import hashlib
import argparse
import json
import math
from pathlib import Path
from typing import Any

from .contracts import BenchmarkOutcome, BenchmarkScore
from .native_trial_binding import bind_native_trace
from .v2.schema import EvalSchemaError


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise EvalSchemaError("official score must be finite and nonnegative")
    return float(value)


def import_trial(task_id: str, trial_dir: Path, *, expected_task_checksum: str,
                 expected_rubric_weights: tuple[float, ...]) -> dict[str, Any]:
    """Checksum must come from the frozen task, not from the imported result.

    Judge details may contain private rubric text; keep this report in control
    plane storage. This validates consistency, not cryptographic judge trust.
    """
    root = trial_dir.resolve(strict=True)
    expected_weights = tuple(_number(value) for value in expected_rubric_weights)
    if not expected_weights or sum(expected_weights) <= 0:
        raise EvalSchemaError("frozen rubric weights are required")
    evidence = {}

    def read(relative: str) -> dict[str, Any]:
        path = root / relative
        if any(part.is_symlink() for part in [path, *path.parents] if part != root and part.is_relative_to(root)):
            raise EvalSchemaError("trial evidence must not contain symlinks")
        raw = path.read_bytes()
        evidence[relative] = "sha256:" + hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise EvalSchemaError("official report must be an object")
        return value

    trial = read("result.json")
    if trial.get("task_name") != f"jobbench/{task_id}" or trial.get("task_checksum") != expected_task_checksum:
        raise EvalSchemaError("trial task identity or frozen checksum mismatch")
    if not expected_task_checksum:
        raise EvalSchemaError("frozen task checksum is required")
    exception = trial.get("exception_info") or {}
    execution_status = "unknown"
    if exception.get("exception_type") == "AgentTimeoutError":
        execution_status = "timed_out"
    elif (trial.get("agent_execution") or {}).get("finished_at"):
        execution_status = "finished"
    report: dict[str, Any] = {
        "schema_version": "praxile.jobbench_import.v1", "task_checksum": expected_task_checksum,
        "execution_status": execution_status, "executor_exception": exception or None,
        "usage": trial.get("agent_result"), "official_details": None,
    }
    status = "infra_error"
    scores: tuple[BenchmarkScore, ...] = ()
    reason = "official verification unavailable"
    verification_path = root / "verifier/verification.json"
    if verification_path.exists():
        verification = read("verifier/verification.json")
        report["verification"] = verification
        if verification.get("status") == "ok":
            if not trial.get("finished_at") or trial.get("verifier_environment_mode") != "separate":
                raise EvalSchemaError("successful import requires finished trial and separate verifier")
            reward = read("verifier/reward.json")
            details = read("verifier/judge-details.json")
            if details.get("error"):
                raise EvalSchemaError("judge error cannot publish a valid score")
            total, maximum = _number(details.get("total_score")), _number(details.get("max_score"))
            rubrics = details.get("rubrics")
            if not isinstance(rubrics, list) or not rubrics or len(rubrics) != details.get("total_count"):
                raise EvalSchemaError("incomplete rubric results")
            indices, weights, values = set(), [], []
            for rubric in rubrics:
                if not isinstance(rubric, dict):
                    raise EvalSchemaError("invalid rubric result")
                index = rubric.get("index")
                if type(index) is not int or index < 0 or index in indices:
                    raise EvalSchemaError("invalid or duplicate rubric index")
                indices.add(index)
                weight = _number(rubric.get("weight"))
                result = rubric.get("result")
                if not isinstance(result, dict) or type(result.get("passed")) is not bool:
                    raise EvalSchemaError("rubric verdict is missing")
                score = _number(result.get("score"))
                if score != (weight if result["passed"] else 0):
                    raise EvalSchemaError("rubric score disagrees with official all-or-nothing verdict")
                weights.append(weight)
                values.append(score)
            if indices != set(range(len(rubrics))) or not math.isclose(sum(weights), maximum) or not math.isclose(sum(values), total):
                raise EvalSchemaError("rubric totals disagree with official summary")
            if len(rubrics) != len(expected_weights) or any(
                not math.isclose(_number(item["weight"]), expected_weights[item["index"]]) for item in rubrics
            ):
                raise EvalSchemaError("reported weights differ from frozen official rubric")
            if maximum <= 0 or total > maximum:
                raise EvalSchemaError("invalid official score denominator")
            normalized = round(total / maximum, 4)
            harbor_reward = (trial.get("verifier_result") or {}).get("rewards", {}).get("reward")
            for value in (reward.get("reward"), verification.get("reward"), harbor_reward):
                if not math.isclose(_number(value), normalized, rel_tol=0, abs_tol=1e-9):
                    raise EvalSchemaError("official reward files disagree")
            scores = (BenchmarkScore("official_weighted_score", total, maximum),
                      BenchmarkScore("official_normalized_reward", normalized, 1))
            status, reason = "completed", "official verifier accepted; not a Praxile self-judgment"
            report["official_details"] = details
        else:
            reason = "official verifier reported an error"
    elif exception.get("exception_type") == "VerifierTimeoutError":
        status, reason = "timed_out", "official verifier timed out"
    from .native_artifacts import audit_output
    report["output_retention"] = audit_output(root, trial)
    report["native_trace_binding"] = bind_native_trace(trial, read, evidence,
                                                       retention=report["output_retention"])
    report["outcome"] = BenchmarkOutcome(task_id, status, scores, tuple(evidence)).to_dict()
    report["reason"] = reason
    report["evidence_digests"] = evidence
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dirhash import dirhash
    from .jobbench import JobBenchGeneration
    generation = JobBenchGeneration(args.generation, manifest_sha256=args.manifest_sha256)
    task, = generation.select(task_ids=(args.task_id,))
    path = generation.paths[task.task_id]
    rubrics = json.loads((path / "tests/RUBRICS.json").read_text(encoding="utf-8"))["rubrics"]
    report = import_trial(task.task_id, args.trial, expected_task_checksum=dirhash(path, "sha256"),
                          expected_rubric_weights=tuple(item["weight"] for item in rubrics))
    report["generation_manifest_sha256"] = generation.manifest_sha256
    report["task_fingerprint"] = task.fingerprint
    import os
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(report, handle, indent=2)
    print(f"Imported {task.task_id}: {report['outcome']['status']}")


if __name__ == "__main__":
    main()
