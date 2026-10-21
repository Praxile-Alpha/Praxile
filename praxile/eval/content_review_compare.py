"""Descriptive comparison of caller-supplied development reviews, never promotion."""
import json
import re

from .content_review import _digest


def verdict_policy(review):
    policies = set()
    for group in review.get("groups", []):
        version, digest = group.get("verdict_policy_version"), group.get("verdict_policy_sha256")
        if version is None and digest is None:
            policies.add((None, None))
        elif (not isinstance(version, str) or not version or len(version) > 100
              or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid verdict policy identity")
        else:
            policies.add((version, digest))
    if len(policies) > 1:
        raise ValueError("mixed verdict policies in one batch")
    return next(iter(policies), (None, None))


def _checked(review):
    if not isinstance(review, dict) or review.get("schema_version") != "praxile.content_review_batch.v1":
        raise ValueError("unsupported review")
    plan = review.get("plan")
    if not isinstance(plan, dict) or review.get("plan_sha256") != _digest(json.dumps(plan, sort_keys=True)):
        raise ValueError("review plan digest mismatch")
    required = {"schema_version", "source_text_sha256", "output_text_sha256", "requirements_sha256",
                "role", "private", "response_mode", "citation_mode", "allocation_policy",
                "total_output_tokens", "timeout_per_call", "groups"}
    if set(plan) != required or plan["schema_version"] != "praxile.content_review_batch_plan.v1":
        raise ValueError("unsupported review plan")
    for key in ("source_text_sha256", "output_text_sha256", "requirements_sha256"):
        if not isinstance(plan[key], str) or not re.fullmatch(r"[0-9a-f]{64}", plan[key]):
            raise ValueError("invalid snapshot digest")
    groups = plan["groups"]
    if not isinstance(groups, list) or not 1 <= len(groups) <= 32:
        raise ValueError("invalid plan groups")
    ids, budget = [], 0
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {"ids", "max_tokens"}
                or not isinstance(group["ids"], list) or not group["ids"]
                or any(not isinstance(i, str) or not i for i in group["ids"])
                or type(group["max_tokens"]) is not int or not 1 <= group["max_tokens"] <= 8000):
            raise ValueError("invalid planned group")
        ids.extend(group["ids"])
        budget += group["max_tokens"]
    if len(ids) > 32 or len(set(ids)) != len(ids) or type(plan["total_output_tokens"]) is not int or budget != plan["total_output_tokens"]:
        raise ValueError("invalid coverage or allocation")
    if review.get("status") != "reviewed":
        return plan, None
    if (review.get("unreviewed_ids") != [] or not isinstance(review.get("groups"), list)
            or len(review["groups"]) != len(groups)):
        raise ValueError("incomplete claimed review")
    all_items = []
    for declared, actual in zip(groups, review["groups"]):
        if (not isinstance(actual, dict) or actual.get("status") != "reviewed"
                or actual.get("output_text_sha256") != plan["output_text_sha256"]
                or actual.get("source_text_sha256") != plan["source_text_sha256"]
                or actual.get("max_tokens") != declared["max_tokens"]
                or any(actual.get(key) != plan[key] for key in ("role", "citation_mode", "response_mode"))
                or actual.get("timeout_seconds") != plan["timeout_per_call"]):
            raise ValueError("group evidence mismatch")
        items = actual.get("items")
        if (not isinstance(items, list) or any(not isinstance(i, dict) or not isinstance(i.get("id"), str) for i in items)
                or sorted(i.get("id", "") for i in items) != sorted(declared["ids"])):
            raise ValueError("group coverage mismatch")
        all_items.extend(items)
    if all_items != review.get("items"):
        raise ValueError("combined items mismatch")
    values = {item["id"]: item.get("verdict") for item in all_items}
    if any(not isinstance(v, str) or v not in {"preserved", "missing", "contradicted", "uncertain"} for v in values.values()):
        raise ValueError("invalid verdict")
    return plan, values


def compare_content_reviews(baseline, candidate, *, baseline_judge_sha256, candidate_judge_sha256):
    """Identity hashes are caller declarations, not independently attested provenance."""
    left, lvalues = _checked(baseline)
    right, rvalues = _checked(candidate)
    for identity in (baseline_judge_sha256, candidate_judge_sha256):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("explicit judge identity digest required")
    result = {"schema_version": "praxile.content_review_comparison.v1",
        "scope": "descriptive_requirement_comparison", "status": "inconclusive",
        "provenance_verified": False, "semantic_accuracy_verified": False,
        "official_evaluation": False, "promotion_eligible": False,
        "baseline_judge_sha256": baseline_judge_sha256, "candidate_judge_sha256": candidate_judge_sha256,
        "baseline_plan_sha256": baseline["plan_sha256"], "candidate_plan_sha256": candidate["plan_sha256"]}
    comparable = all(left[key] == right[key] for key in left if key != "output_text_sha256")
    if not comparable or baseline_judge_sha256 != candidate_judge_sha256:
        return {**result, "reason": "conditions_mismatch"}
    if lvalues is None or rvalues is None:
        return {**result, "reason": "incomplete_review"}
    if verdict_policy(baseline) != verdict_policy(candidate):
        return {**result, "reason": "verdict_policy_mismatch"}
    rows = [{"id": key, "baseline": lvalues[key], "candidate": rvalues[key]} for key in lvalues]
    result.update(status="compared", rows=rows, requirement_count=len(rows),
        baseline_preserved=sum(v == "preserved" for v in lvalues.values()),
        candidate_preserved=sum(v == "preserved" for v in rvalues.values()),
        uncertain_rows=sum("uncertain" in (row["baseline"], row["candidate"]) for row in rows))
    return result
