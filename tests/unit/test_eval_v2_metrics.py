from __future__ import annotations

from praxile.eval.v2 import trace_metrics
from praxile.trace import AgentEvent, TokenUsage


def _event(event_id: str, event_type: str, payload: dict, **kwargs):
    return AgentEvent.create(
        event_id=event_id,
        timestamp="2026-01-01T00:00:00+00:00",
        trace_id="trace",
        run_id="run",
        task_id="task",
        type=event_type,
        actor=kwargs.pop("actor", "agent"),
        payload=payload,
        **kwargs,
    )


def test_trace_metrics_cover_frozen_p0_dimensions() -> None:
    metrics = trace_metrics(
        [
            _event("e1", "MODEL_CALL", {"parse_status": "rejected"}, token_usage=TokenUsage(10, 2), cost=0.25),
            _event("e2", "TOOL_CALL", {"tool": "shell"}),
            _event("e3", "VERIFICATION", {"status": "failed"}),
            _event("e4", "VERIFICATION", {"status": "passed"}),
            _event("e5", "CHECKPOINT", {"requires_human_intervention": True}),
        ],
        wall_latency_ms=42,
        resolved=True,
    )

    assert metrics["task_success"] is True
    assert metrics["tokens"] == {"input": 10, "output": 2, "cache": 0}
    assert metrics["cost"] == 0.25
    assert metrics["latency_ms"] == 42
    assert metrics["tool_calls"] == 1
    assert metrics["retries"] == 1
    assert metrics["recoveries"] == 1
    assert metrics["interventions"] == 1
