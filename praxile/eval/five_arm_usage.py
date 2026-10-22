"""Aggregate every bound broker receipt, not only selected execution costs."""
import math
from pathlib import PurePosixPath

from .owned_content_review import _reader, _json, _sha


def executor_receipts(directory, cell, *, conditions=None, task=None):
    read = _reader(directory)
    result = []
    attempts = cell.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("cell attempts missing")
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict) or type(attempt.get("index")) is not int or attempt["index"] != index:
            raise ValueError("unordered cost attempts")
        if attempt.get("status") != "returned":
            result.append(("executor", None))
            continue
        relative = attempt.get("report")
        if not isinstance(relative, str) or not relative.startswith(f"attempt-{index}/"):
            raise ValueError("cost report belongs to a different attempt")
        raw = read(relative)
        if _sha(raw) != attempt.get("sha256"):
            raise ValueError("cost execution receipt changed")
        report = _json(raw)
        if conditions is not None:
            from .five_arm_conditions import check_attempt_conditions, check_attempt_mechanisms
            check_attempt_conditions(report, conditions, task)
            check_attempt_mechanisms(report, cell)
        digest = report.get("broker_usage_sha256")
        if digest is None:
            result.append(("executor", None))
            continue
        raw = read(str(PurePosixPath(relative).parent / "agent/praxile-broker-usage.json"))
        if _sha(raw) != digest:
            raise ValueError("executor broker receipt changed")
        result.append(("executor", _json(raw)))
    return result


def _checked_usage(receipt):
    usage = receipt.get("usage") if isinstance(receipt, dict) else None
    if not isinstance(usage, dict) or usage.get("schema_version") != "praxile.broker_usage.v1":
        raise ValueError("unsupported broker usage receipt")
    counts = ("admitted_calls", "responses", "failed_calls", "inflight_calls")
    if any(type(usage.get(key)) is not int or usage[key] < 0 for key in counts):
        raise ValueError("invalid broker call counts")
    if sum(usage[key] for key in counts[1:]) != usage["admitted_calls"]:
        raise ValueError("broker call conservation mismatch")
    latency = usage.get("latency_seconds")
    if type(latency) not in (float, int) or not math.isfinite(latency) or latency < 0:
        raise ValueError("invalid broker latency")
    for key in ("known_tokens", "total_tokens", "reported_calls"):
        if not isinstance(usage.get(key), dict) or set(usage[key]) != {"input", "output"}:
            raise ValueError("invalid broker token fields")
    for side in ("input", "output"):
        known, reported, total = (usage[key][side] for key in ("known_tokens", "reported_calls", "total_tokens"))
        if (type(known) is not int or known < 0 or type(reported) is not int
                or not 0 <= reported <= usage["responses"] or (reported == 0 and known != 0)):
            raise ValueError("invalid broker token coverage")
        if reported == usage["admitted_calls"]:
            if type(total) is not int or total != known:
                raise ValueError("invalid complete broker token total")
        elif total is not None:
            raise ValueError("partial broker usage cannot declare a complete total")
    return usage


def summarize_receipts(receipts):
    roles = {}
    for role, receipt in receipts:
        if role not in {"executor", "judge"}:
            raise ValueError("unsupported study cost role")
        item = roles.setdefault(role, {"expected_receipts": 0, "bound_receipts": 0,
            "known_admitted_calls": 0, "known_request_latency_seconds": 0.0,
            "known_tokens": {"input": 0, "output": 0}, "reported_calls": {"input": 0, "output": 0}})
        item["expected_receipts"] += 1
        if receipt is None:
            continue
        usage = _checked_usage(receipt)
        item["bound_receipts"] += 1
        item["known_admitted_calls"] += usage["admitted_calls"]
        item["known_request_latency_seconds"] += usage["latency_seconds"]
        if not math.isfinite(item["known_request_latency_seconds"]):
            raise ValueError("aggregate broker latency overflow")
        for side in ("input", "output"):
            item["known_tokens"][side] += usage["known_tokens"][side]
            item["reported_calls"][side] += usage["reported_calls"][side]
    for item in roles.values():
        complete = item["bound_receipts"] == item["expected_receipts"]
        item["admitted_calls"] = item["known_admitted_calls"] if complete else None
        item["total_tokens"] = {side: item["known_tokens"][side]
            if complete and item["reported_calls"][side] == item["known_admitted_calls"] else None
            for side in ("input", "output")}
    return {"schema_version": "praxile.five_arm_broker_usage.v1", "roles": roles,
        "scope": "bound_executor_attempts_and_judge_broker_receipts",
        "known_tokens": {side: sum(item["known_tokens"][side] for item in roles.values()) for side in ("input", "output")},
        "total_tokens": {side: sum(item["total_tokens"][side] for item in roles.values())
            if roles and all(item["total_tokens"][side] is not None for item in roles.values()) else None
            for side in ("input", "output")},
        "cost_usd": None, "billing_verified": False, "total_token_bound_verified": False,
        "includes_upstream_proposer_or_composer": False}
