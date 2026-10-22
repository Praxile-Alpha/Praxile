"""Precommitted artifact selection, never quality judging or promotion."""
import hashlib
import json
from pathlib import Path

from .artifact_read import read_artifact
from .v2.schema import canonical_json


def selection_policy(strategy):
    if not isinstance(strategy, str) or strategy not in {"first_delivered", "last_delivered"}:
        raise ValueError("unsupported development selection strategy")
    return {"schema_version": "praxile.five_arm_selection.v1", "strategy": strategy,
            "eligibility": "executor_returned_cleanup_and_required_outputs",
            "uses_quality_scores": False}


def validate_selection(policy, plan):
    if not isinstance(policy, dict) or policy != selection_policy(policy.get("strategy")):
        raise ValueError("invalid selection policy")
    digest = "sha256:" + hashlib.sha256(canonical_json(policy).encode()).hexdigest()
    if plan["spec"]["identities"]["terminal_selection"] != digest:
        raise ValueError("selection policy differs from frozen study identity")
    return digest


def select_delivered(attempts, directory, policy):
    """Verify every receipt, then select by sequence without loading judge files."""
    if not isinstance(policy, dict) or policy != selection_policy(policy.get("strategy")):
        raise ValueError("invalid selection policy")
    root = Path(directory)
    info = root.stat()
    eligible = []
    for index, attempt in enumerate(attempts):
        if type(attempt.get("index")) is not int or attempt["index"] != index or attempt.get("status") != "returned":
            raise ValueError("selection requires ordered returned attempts")
        relative = attempt.get("report")
        if not isinstance(relative, str) or not relative.startswith(f"attempt-{index}/"):
            raise ValueError("attempt report must belong to its own execution")
        raw = read_artifact(root, relative, root_identity=(info.st_dev, info.st_ino))
        if hashlib.sha256(raw).hexdigest() != attempt.get("sha256"):
            raise ValueError("attempt receipt changed before selection")
        report = json.loads(raw)
        if not isinstance(report, dict):
            raise ValueError("attempt report must be an object")
        if (report.get("status") == "executor_returned" and report.get("cleanup_verified") is True
                and report.get("required_outputs_collected") is True):
            eligible.append(index)
    selected = None if not eligible else eligible[0 if policy["strategy"] == "first_delivered" else -1]
    return {"schema_version": "praxile.five_arm_selection_result.v1", "policy": policy,
            "eligible_attempts": eligible, "selected_attempt": selected,
            "reason": "delivery_contract_only" if eligible else "no_delivered_attempt",
            "quality_verified": False, "promotion_eligible": False, "uses_quality_scores": False}
