"""Audited, descriptive online task summaries; no IID or causal assumptions."""
from .online_review import audit_online_review
from .online_stream import OnlineStream


def summarize_online_stream(directory, reviews):
    stream = OnlineStream(directory)
    before = stream.status()
    if before["pending_index"] is not None:
        raise ValueError("online summary requires an idle stream")
    if not isinstance(reviews, list):
        raise ValueError("reviews must be an explicit list")
    completed = before["completed_task_ids"]
    indexed, protocols = {}, set()
    for entry in reviews:
        if not isinstance(entry, dict) or set(entry) != {"task_index", "directory", "receipt_sha256", "judge"}:
            raise ValueError("invalid online review entry")
        index = entry["task_index"]
        if type(index) is not int or not 0 <= index < len(completed) or index in indexed:
            raise ValueError("review index must be unique and completed")
        audit = audit_online_review(directory, index, judge=entry["judge"], directory=entry["directory"],
                                    receipt_sha256=entry["receipt_sha256"])
        indexed[index] = audit
        protocols.add(audit["judge_protocol_sha256"])
    if len(protocols) > 1:
        raise ValueError("online summary cannot mix judge protocols")
    rows, scores = [], []
    for index in range(before["task_count"]):
        row = {"task_index": index, "status": "not_executed", "preserved_fraction": None}
        if index < len(completed):
            row.update(task_id=completed[index], status="missing_review")
        if index in indexed:
            audit = indexed[index]
            row.update(status=audit["status"], verdict_counts=audit["verdict_counts"],
                       receipt_sha256=audit["receipt_sha256"], requirement_count=audit["requirement_count"])
            if audit["status"] == "complete_judge_labels":
                row["preserved_fraction"] = audit["verdict_counts"]["preserved"] / audit["requirement_count"]
                scores.append(row["preserved_fraction"])
        rows.append(row)
    if stream.status() != before:
        raise ValueError("stream changed during summary; recompute against a stable ledger")
    total, observed = before["task_count"], len(scores)
    return {"schema_version": "praxile.online_stream_summary.v1", "track": "online_stream",
        "source_ledger_sha256": before["ledger_sha256"], "rows": rows,
        "planned_tasks": total, "completed_tasks": len(completed), "audited_reviews": len(indexed),
        "tasks_with_complete_labels": observed, "unknown_or_unexecuted_tasks": total - observed,
        "complete_label_coverage": observed / total,
        "observed_task_mean": sum(scores) / observed if observed else None,
        "planned_task_mean_bounds": {"lower": sum(scores) / total,
                                     "upper": (sum(scores) + total - observed) / total},
        "bounds_kind": "missing_outcome_bounds_not_confidence_interval",
        "judge_precommitted_before_execution": before["judge_precommitted_before_execution"],
        "judge_protocol_sha256": next(iter(protocols), None),
        "metric": "llm_declared_requirement_preservation", "task_weighting": "equal",
        "independent_task_assumption": False, "causal_improvement_estimated": False,
        "official_evaluation": False, "semantic_accuracy_verified": False, "promotion_eligible": False}
