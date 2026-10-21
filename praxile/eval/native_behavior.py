"""Descriptive development behavior evidence; no evaluator, model or causal score."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .v2.native_development_evidence import collect_native_evidence


def summarize_native_behavior(path, *, expected_sha256, development_task_ids, heldout_task_ids):
    bundle = collect_native_evidence(path, expected_sha256=expected_sha256,
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids,
        track="development", all_eligible=True)
    counts = Counter()
    repeated = 0
    timeline = []
    for evidence in bundle["evidence"]:
        facts = json.loads(evidence["observation"])
        counts[(facts["operation"], facts["status"])] += 1
        repeated += facts["same_parameter_occurrence"] > 1
        timeline.append({"event_id": evidence["event_id"], "facts": facts})
    return {"schema_version": "praxile.native_behavior.v1",
        "task_id": bundle["task_id"], "source_result_sha256": bundle["source_result_sha256"],
        "projection_version": bundle["projection_version"],
        "scope": "eligible_structured_tool_results_in_recorded_order",
        "source_tool_result_count": bundle["source_tool_result_count"],
        "projected_count": len(timeline),
        "unprojected_tool_results": bundle["source_tool_result_count"] - len(timeline),
        "operation_status_counts": [{"operation": op, "status": status, "count": count}
            for (op, status), count in sorted(counts.items())],
        "repeated_same_parameter_observations": repeated,
        "timeline": timeline, "sources": bundle["sources"],
        "provenance_attested": False, "causal_effect_verified": False,
        "task_success_verified": False, "promotion_eligible": False}


def compare_native_behavior(baseline_path, candidate_path, *, baseline_sha256,
                            candidate_sha256, development_task_ids, heldout_task_ids):
    """Compare projected counts, not positional steps or task-quality verdicts."""
    arms = {}
    for arm, path, digest in (("baseline", baseline_path, baseline_sha256),
                              ("candidate", candidate_path, candidate_sha256)):
        arms[arm] = summarize_native_behavior(path, expected_sha256=digest,
            development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids)
    counts = {arm: {(row["operation"], row["status"]): row["count"]
                    for row in report["operation_status_counts"]}
              for arm, report in arms.items()}
    left, right = counts["baseline"], counts["candidate"]
    return {"schema_version": "praxile.native_behavior_comparison.v1",
        "scope": "descriptive_projected_operation_counts", "arms": arms,
        "deltas": [{"operation": op, "status": status,
                    "baseline": left.get((op, status), 0),
                    "candidate": right.get((op, status), 0),
                    "candidate_minus_baseline": right.get((op, status), 0) - left.get((op, status), 0)}
                   for op, status in sorted(set(left) | set(right))],
        "experimental_conditions_verified": False, "semantic_alignment_verified": False,
        "causal_effect_verified": False, "task_success_verified": False,
        "promotion_eligible": False,
        "limitations": ["Zero means absent from the projection, not necessarily absent from execution.",
            "Repeated parameters do not establish unchanged state or unnecessary work.",
            "Lower action counts do not prove better output or experience benefit.",
            "Task membership and pairing are caller-declared; no evaluator is read."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--development-task-id", action="append", required=True)
    parser.add_argument("--heldout-task-id", action="append", default=[])
    parser.add_argument("--candidate-result", type=Path)
    parser.add_argument("--candidate-sha256")
    args = parser.parse_args()
    if bool(args.candidate_result) != bool(args.candidate_sha256):
        parser.error("--candidate-result and --candidate-sha256 must be supplied together")
    membership = dict(development_task_ids=args.development_task_id,
                      heldout_task_ids=args.heldout_task_id)
    if args.candidate_result:
        result = compare_native_behavior(args.result, args.candidate_result,
            baseline_sha256=args.sha256, candidate_sha256=args.candidate_sha256, **membership)
    else:
        result = summarize_native_behavior(args.result, expected_sha256=args.sha256, **membership)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
