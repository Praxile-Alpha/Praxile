from __future__ import annotations

from typing import Any, Iterable

from ...trace import AgentEvent


def trace_metrics(events: Iterable[AgentEvent], *, wall_latency_ms: int, resolved: bool | None) -> dict[str, Any]:
    rows = list(events)
    model_calls = [event for event in rows if event.type == "MODEL_CALL"]
    verifications = [event for event in rows if event.type == "VERIFICATION"]
    verification_states = [str(event.payload.get("status") or "unknown") for event in verifications]
    recovery_count = 0
    failed_interval = False
    for state in verification_states:
        if state == "failed":
            failed_interval = True
        elif state == "passed" and failed_interval:
            recovery_count += 1
            failed_interval = False
    return {
        "task_success": resolved,
        "tokens": {
            "input": sum(event.token_usage.input for event in model_calls if event.token_usage),
            "output": sum(event.token_usage.output for event in model_calls if event.token_usage),
            "cache": sum(event.token_usage.cache for event in model_calls if event.token_usage),
        },
        "cost": round(sum(float(event.cost or 0.0) for event in model_calls), 8),
        "latency_ms": wall_latency_ms,
        "reported_model_latency_ms": sum(int(event.latency_ms or 0) for event in model_calls),
        "model_calls": len(model_calls),
        "tool_calls": sum(1 for event in rows if event.type == "TOOL_CALL"),
        "retries": sum(
            1
            for event in model_calls
            if event.payload.get("parse_status") == "rejected" or bool(event.payload.get("retry"))
        ),
        "recoveries": recovery_count,
        "interventions": sum(
            1
            for event in rows
            if event.actor.lower().startswith("human") or bool(event.payload.get("requires_human_intervention"))
        ),
        "verification": {
            "total": len(verifications),
            "passed": verification_states.count("passed"),
            "failed": verification_states.count("failed"),
            "unknown": verification_states.count("unknown"),
        },
    }


def aggregate_metrics(task_results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [item.get("metrics", {}) for item in task_results]
    resolved = [item for item in task_results if item.get("evaluator", {}).get("resolved") is True]
    known = [item for item in task_results if item.get("evaluator", {}).get("resolved") is not None]
    return {
        "task_count": len(task_results),
        "completed_count": sum(1 for item in task_results if item.get("status") == "completed"),
        "resolved_count": len(resolved),
        "resolution_rate": round(len(resolved) / len(known), 4) if known else None,
        "unknown_evaluation_count": len(task_results) - len(known),
        "tokens": {
            key: sum(int(item.get("tokens", {}).get(key, 0)) for item in metrics)
            for key in ("input", "output", "cache")
        },
        "cost": round(sum(float(item.get("cost", 0.0)) for item in metrics), 8),
        "latency_ms": sum(int(item.get("latency_ms", 0)) for item in metrics),
        "tool_calls": sum(int(item.get("tool_calls", 0)) for item in metrics),
        "retries": sum(int(item.get("retries", 0)) for item in metrics),
        "recoveries": sum(int(item.get("recoveries", 0)) for item in metrics),
        "interventions": sum(int(item.get("interventions", 0)) for item in metrics),
    }
