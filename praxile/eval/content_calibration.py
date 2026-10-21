"""Labeled development checks for the content judge, separate from promotion."""
import json

from .content_review import _digest, _validate_inputs, review_content


def calibrate_content_review(router, logger, *, cases, max_tokens_per_case,
                             role="content_review_judge", private=True,
                             timeout_per_case=90, response_mode="function", citation_mode="units"):
    """Labels stay in this controller, not in messages or review requirements.

    Caller-supplied labels are not independently authenticated ground truth.
    Case IDs must not encode labels: they are for local result association only.
    """
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        raise ValueError("calibration requires 1..32 explicit cases")
    if citation_mode not in ("quote", "units"):
        raise ValueError("unsupported citation mode")
    frozen = []
    ids = set()
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {
                "id", "source_text", "output_text", "requirements", "expected"}
                or not isinstance(case["id"], str) or not 1 <= len(case["id"]) <= 80
                or case["id"] in ids):
            raise ValueError("invalid or duplicate calibration case")
        expected_inputs = _validate_inputs(case["source_text"], case["output_text"],
            case["requirements"], role, private, max_tokens_per_case, timeout_per_case, response_mode, None)
        labels = case["expected"]
        if (not isinstance(labels, dict) or set(labels) != set(expected_inputs)
                or any(not isinstance(v, str) or v not in {"preserved", "missing", "contradicted"}
                       for v in labels.values())):
            raise ValueError("reference labels must cover every requirement")
        ids.add(case["id"])
        frozen.append({"id": case["id"], "source_text": case["source_text"],
            "output_text": case["output_text"], "requirements": list(expected_inputs.values()),
            "expected": dict(labels)})
    record = {"schema_version": "praxile.content_calibration.v1",
        "suite_sha256": _digest(json.dumps(frozen, sort_keys=True)),
        "scope": "caller_labeled_development_cases", "reference_labels_verified": False,
        "case_count": len(frozen), "max_logical_requests": len(frozen),
        "max_requested_output_tokens": len(frozen) * max_tokens_per_case,
        "role": role, "response_mode": response_mode, "citation_mode": citation_mode,
        "timeout_per_case": timeout_per_case, "rows": [], "status": "running",
        "promotion_eligible": False, "official_evaluation": False}
    logger.data.setdefault("content_calibrations", []).append(record)
    for case in frozen:
        review = review_content(router, logger, source_text=case["source_text"],
            output_text=case["output_text"], requirements=case["requirements"], role=role,
            private=private, max_tokens=max_tokens_per_case, timeout=timeout_per_case,
            response_mode=response_mode, citation_mode=citation_mode)
        predictions = {item["id"]: item["verdict"] for item in review.get("items", [])}
        for key, expected in case["expected"].items():
            prediction = predictions.get(key)
            record["rows"].append({"case_id": case["id"], "requirement_id": key,
                "expected": expected, "predicted": prediction,
                "abstained": prediction in {None, "uncertain"}, "review_status": review["status"],
                "review_input_sha256": review["input_sha256"]})
    rows = record["rows"]
    decided = [row for row in rows if not row["abstained"]]
    correct = sum(row["predicted"] == row["expected"] for row in decided)
    positive = [row for row in decided if row["predicted"] == "preserved"]
    false_preserved = sum(row["expected"] != "preserved" for row in positive)
    record.update(status="completed", requirement_count=len(rows), decided_count=len(decided),
        abstention_count=len(rows) - len(decided), correct_count=correct,
        coverage=len(decided) / len(rows), accuracy_including_abstention=correct / len(rows),
        conditional_accuracy=correct / len(decided) if decided else None,
        preserved_precision=(len(positive) - false_preserved) / len(positive) if positive else None,
        false_preserved_count=false_preserved,
        false_preserved_rate=false_preserved / len(positive) if positive else None,
        probability_calibration_error=None)
    return record
