from __future__ import annotations

from praxile.eval.v2 import trace_metrics
from praxile.trace import AgentEvent, TokenUsage
from praxile.eval.v2.metrics import aggregate_metrics
from praxile.eval.v2.experiment import _tokens


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


def test_missing_price_preserves_subtotal_but_total_is_unknown():
    metrics = trace_metrics([_event("known", "MODEL_CALL", {}, cost=0.25),
                             _event("unknown", "MODEL_CALL", {})], wall_latency_ms=1, resolved=True)
    assert metrics["reported_cost_subtotal"] == 0.25
    assert metrics["cost_total"] is None
    assert not metrics["cost_complete"]
    assert metrics["unpriced_model_calls"] == 1
    combined = aggregate_metrics([{"metrics": metrics}])
    assert combined["cost_total"] is None
    assert combined["unpriced_task_count"] == 1


def test_explicit_zero_price_is_distinct_from_absent_model_events():
    zero = trace_metrics([_event("free", "MODEL_CALL", {}, cost=0.0)], wall_latency_ms=1, resolved=True)
    assert zero["cost_complete"] is True
    assert zero["cost_total"] == 0
    assert aggregate_metrics([{"metrics": zero}])["cost_total"] == 0
    empty = trace_metrics([], wall_latency_ms=1, resolved=None)
    assert empty["cost_total"] is None


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


def test_missing_token_events_remain_unknown_through_aggregation_and_ab():
    metrics = trace_metrics([_event("known", "MODEL_CALL", {}, token_usage=TokenUsage(10, 2)),
                             _event("unknown", "MODEL_CALL", {})], wall_latency_ms=1, resolved=True)
    assert metrics["tokens"] == {"input": 10, "output": 2, "cache": 0}
    assert metrics["tokens_total"] is None
    assert metrics["unreported_token_calls"] == 1
    assert _tokens({"metrics": metrics}) is None
    combined = aggregate_metrics([{"metrics": metrics}])
    assert combined["tokens_total"] is None
    assert combined["token_complete_task_count"] == 0
    assert _tokens({"metrics": combined}) is None


def test_explicit_zero_usage_is_not_empty_trace():
    zero = trace_metrics([_event("zero", "MODEL_CALL", {}, token_usage=TokenUsage())], wall_latency_ms=1, resolved=True)
    assert zero["tokens_complete"] is True
    assert _tokens({"metrics": zero}) == 0
    assert aggregate_metrics([{"metrics": zero}])["tokens_total"] == zero["tokens"]
    empty = trace_metrics([], wall_latency_ms=1, resolved=None)
    assert empty["tokens_total"] is None
    assert aggregate_metrics([])["tokens_complete"] is False


def test_old_and_malformed_subtotals_do_not_claim_aggregate_completeness():
    result = aggregate_metrics([{"metrics": {"tokens": {"input": 1, "output": None, "cache": 0}}}])
    assert result["tokens_total"] is None
    assert result["tokens"]["input"] == 1
