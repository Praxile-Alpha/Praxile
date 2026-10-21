"""Deterministic requirement partitioning with an explicit total output allowance."""
import json

from .content_review import _digest, _validate_inputs, review_content


def review_content_batched(router, logger, *, source_text, output_text, requirements,
                           total_output_tokens, batch_size=4, timeout_per_call=90,
                           role="content_review_judge", private=True,
                           response_mode="function", response_checkpoint=None, citation_mode="quote"):
    """Stop on first abstention; never recycle unused allowance or retry groups.

    This caps requested output tokens across logical calls, not input tokens,
    money, wall time, provider billing or transport retries. Configure transport
    retries/fallbacks separately. Every group sees the same full text snapshots.
    """
    if (type(batch_size) is not int or not 1 <= batch_size <= 32
            or type(total_output_tokens) is not int or not 1 <= total_output_tokens <= 256000):
        raise ValueError("invalid batch or total output budget")
    expected = _validate_inputs(source_text, output_text, requirements, role, private,
                                1, timeout_per_call, response_mode, response_checkpoint)
    if citation_mode not in ("quote", "units"):
        raise ValueError("unsupported citation mode")
    frozen = list(expected.values())
    groups = [frozen[i:i + batch_size] for i in range(0, len(frozen), batch_size)]
    base, remainder = divmod(total_output_tokens, len(groups))
    budgets = [base + (i < remainder) for i in range(len(groups))]
    if min(budgets) < 1 or max(budgets) > 8000:
        raise ValueError("each allocated output budget must be between 1 and 8000")
    plan = {"schema_version": "praxile.content_review_batch_plan.v1",
        "source_text_sha256": _digest(source_text), "output_text_sha256": _digest(output_text),
        "requirements_sha256": _digest(json.dumps(frozen, sort_keys=True)),
        "role": role, "private": private, "response_mode": response_mode,
        "citation_mode": citation_mode,
        "total_output_tokens": total_output_tokens, "timeout_per_call": timeout_per_call,
        "groups": [{"ids": [item["id"] for item in group], "max_tokens": budget}
                   for group, budget in zip(groups, budgets)]}
    record = {"schema_version": "praxile.content_review_batch.v1", "plan": plan,
        "plan_sha256": _digest(json.dumps(plan, sort_keys=True)), "status": "pending",
        "groups": [], "logical_requests": 0, "allocated_output_tokens_requested": 0,
        "unreviewed_ids": list(expected), "official_evaluation": False,
        "promotion_eligible": False, "content_preservation_verified": False,
        "requirement_coverage_verified": False}
    logger.data.setdefault("content_review_batches", []).append(record)
    combined = []
    for index, (group, budget) in enumerate(zip(groups, budgets)):
        checkpoint = None
        if response_checkpoint is not None:
            def checkpoint(payload, group_index=index):
                response_checkpoint({**payload, "batch_plan_sha256": record["plan_sha256"],
                                     "group_index": group_index})
        record["logical_requests"] += 1
        record["allocated_output_tokens_requested"] += budget
        result = review_content(router, logger, source_text=source_text, output_text=output_text,
            requirements=group, role=role, private=private, max_tokens=budget,
            timeout=timeout_per_call, response_mode=response_mode, response_checkpoint=checkpoint,
            citation_mode=citation_mode)
        record["groups"].append(result)
        if result["status"] != "reviewed":
            record.update(status="abstained", failed_group_index=index)
            return record
        combined.extend(result["items"])
        reviewed = {item["id"] for item in combined}
        record["unreviewed_ids"] = [key for key in expected if key not in reviewed]
    record.update(status="reviewed", items=combined, judgment_source="llm",
                  all_declared_requirements_preserved=all(
                      item["verdict"] == "preserved" for item in combined))
    return record
