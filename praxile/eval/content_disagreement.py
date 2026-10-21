"""Triage judge disagreements on one frozen artifact, without voting or promotion."""
import argparse
import json
from pathlib import Path
import re

from .content_review_compare import _checked
from .content_review_files import load_review_run


def compare_judgments(left, right, *, left_judge_sha256, right_judge_sha256):
    plans, values = zip(_checked(left), _checked(right))
    for identity in (left_judge_sha256, right_judge_sha256):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("explicit judge identity digest required")
    result = {"schema_version": "praxile.content_disagreement.v1",
        "scope": "same_artifact_judgment_triage", "status": "inconclusive",
        "left_judge_sha256": left_judge_sha256, "right_judge_sha256": right_judge_sha256,
        "left_plan_sha256": left["plan_sha256"], "right_plan_sha256": right["plan_sha256"],
        "judge_identity_differs": left_judge_sha256 != right_judge_sha256,
        "reference_labels_verified": False, "semantic_accuracy_verified": False,
        "provenance_verified": False, "promotion_eligible": False,
        "official_evaluation": False, "winner": None}
    snapshot_keys = ("source_text_sha256", "output_text_sha256", "requirements_sha256")
    if any(plans[0][key] != plans[1][key] for key in snapshot_keys):
        return {**result, "reason": "snapshot_mismatch"}
    result["snapshot"] = {key: plans[0][key] for key in snapshot_keys}
    result["condition_differences"] = [{"field": key, "left": plans[0][key], "right": plans[1][key]}
        for key in sorted(plans[0]) if key not in snapshot_keys and plans[0][key] != plans[1][key]]
    if values[0] is None or values[1] is None:
        return {**result, "reason": "incomplete_review"}
    if set(values[0]) != set(values[1]):
        raise ValueError("same requirement digest with inconsistent coverage")
    rows = [{"id": key, "left": values[0][key], "right": values[1][key],
             "disagrees": values[0][key] != values[1][key],
             "needs_human_review": values[0][key] != values[1][key]
                 or "uncertain" in (values[0][key], values[1][key]),
             "reference_verdict": None}
            for key in values[0]]
    result.update(status="triaged", rows=rows, requirement_count=len(rows),
        disagreement_count=sum(row["disagrees"] for row in rows),
        review_required_ids=[row["id"] for row in rows if row["needs_human_review"]],
        limitations=["Agreement is not evidence of correctness; labels require independent review.",
            "Judge identity hashes are caller declarations, not authenticated provider identities.",
            "Different budgets, grouping or reasoning may confound provider comparisons.",
            "No votes, automatic adjudication, accuracy score or promotion is produced."])
    return result


def compare_judgment_runs(left_directory, right_directory):
    left, left_identity = load_review_run(left_directory)
    right, right_identity = load_review_run(right_directory)
    result = compare_judgments(left, right, left_judge_sha256=left_identity,
                               right_judge_sha256=right_identity)
    result["local_file_consistency_checked"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_judgment_runs(args.left, args.right)
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "Review evidence is missing, inconsistent or unsupported; triage refused.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "triaged" else 1


if __name__ == "__main__":
    raise SystemExit(main())
