from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..interop import PRAXILE_TRAJECTORY_SCHEMA
from ..utils import stable_hash, utc_now
from .schema import AGENT_EVENT_SCHEMA_VERSION, AgentEvent, TokenUsage


def v1_trajectory_to_events(trajectory: dict[str, Any]) -> list[AgentEvent]:
    """Project a V1 trajectory into deterministic V2 events.

    Repeating the import produces the same event IDs, which makes EventStore
    ingestion idempotent. The V1 source remains untouched.
    """

    task_id = str(trajectory.get("task_id") or _deterministic_id("task", trajectory))
    start_time = _time(trajectory.get("start_time"))
    end_time = _time(trajectory.get("end_time"), fallback=start_time)
    trace_id = str(trajectory.get("trace_id") or _deterministic_id("trace", {"task_id": task_id, "start": start_time}))
    run_id = str(trajectory.get("run_id") or _deterministic_id("run", {"task_id": task_id, "start": start_time}))
    events: list[AgentEvent] = []

    def add(
        type: str,
        actor: str,
        payload: dict[str, Any],
        *,
        timestamp: str | None = None,
        step_id: str | None = None,
        tool_call_id: str | None = None,
        artifact_ids: tuple[str, ...] = (),
        token_usage: TokenUsage | None = None,
        cost: float | None = None,
        backend_sequence: int | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> AgentEvent:
        sequence = len(events)
        identity = {
            "task_id": task_id,
            "trace_id": trace_id,
            "run_id": run_id,
            "sequence": sequence,
            "type": type,
            "payload": payload,
        }
        event = AgentEvent(
            schema_version=AGENT_EVENT_SCHEMA_VERSION,
            event_id=_deterministic_id("event", identity),
            timestamp=timestamp or start_time,
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            type=type,
            actor=actor,
            payload=payload,
            evidence_refs=evidence_refs,
            step_id=step_id,
            tool_call_id=tool_call_id,
            artifact_ids=artifact_ids,
            token_usage=token_usage,
            cost=cost,
            backend_sequence=sequence if backend_sequence is None else backend_sequence,
            native_payload_ref=str(trajectory.get("external_compat", {}).get("source_of_truth") or "") or None,
            extensions={"compatibility_source": PRAXILE_TRAJECTORY_SCHEMA},
        )
        events.append(event)
        return event

    add(
        "RUN_START",
        "agent",
        {
            "user_task": str(trajectory.get("user_task") or "Imported V1 trajectory"),
            "environment_snapshot": trajectory.get("environment_snapshot") or {},
            "task_analysis": trajectory.get("task_analysis") or {},
            "plan": trajectory.get("plan") or [],
            "legacy_schema": str(trajectory.get("schema") or PRAXILE_TRAJECTORY_SCHEMA),
        },
    )

    loaded_assets = trajectory.get("loaded_assets") or []
    if loaded_assets:
        add(
            "CONTEXT_INJECT",
            "agent",
            {
                "source": "v1_loaded_assets",
                "assets": loaded_assets,
                "experience_control": trajectory.get("experience_control") or {},
            },
        )

    for index, call in enumerate((trajectory.get("model_routing") or {}).get("calls") or [], 1):
        if not isinstance(call, dict):
            continue
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        add(
            "MODEL_CALL",
            "model",
            {key: value for key, value in call.items() if key != "usage"},
            timestamp=_time(call.get("created_at"), fallback=start_time),
            step_id=f"model-{index}",
            token_usage=TokenUsage.from_dict(usage),
            cost=_cost(call.get("cost")),
        )

    for index, action in enumerate(trajectory.get("actions") or [], 1):
        if not isinstance(action, dict):
            continue
        step_id = str(action.get("step") or index)
        tool_call_id = _deterministic_id(
            "toolcall",
            {"trace_id": trace_id, "run_id": run_id, "step_id": step_id, "action": action.get("action_type")},
        )
        action_type = str(action.get("action_type") or "external_action")
        created_at = _time(action.get("created_at"), fallback=start_time)
        call = add(
            "TOOL_CALL",
            "agent",
            {
                "tool": action_type,
                "input": action.get("input") if isinstance(action.get("input"), dict) else {},
                "executor": action.get("executor") if isinstance(action.get("executor"), dict) else {},
            },
            timestamp=created_at,
            step_id=step_id,
            tool_call_id=tool_call_id,
            cost=_cost((action.get("cost") or {}).get("estimated_usd") if isinstance(action.get("cost"), dict) else None),
        )
        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        add(
            "TOOL_RESULT",
            "tool",
            {"tool": action_type, "status": action.get("status"), "observation": observation},
            timestamp=created_at,
            step_id=step_id,
            tool_call_id=tool_call_id,
            evidence_refs=(call.event_id,),
        )

    for index, artifact in enumerate(trajectory.get("artifacts") or [], 1):
        if not isinstance(artifact, dict):
            continue
        artifact_id = str(artifact.get("artifact_id") or _deterministic_id("artifact", {"trace_id": trace_id, "index": index, "artifact": artifact}))
        add(
            "ARTIFACT_CHANGE",
            "environment",
            dict(artifact),
            timestamp=_time(artifact.get("created_at"), fallback=end_time),
            artifact_ids=(artifact_id,),
        )

    report = trajectory.get("reward_report") if isinstance(trajectory.get("reward_report"), dict) else {}
    test_results = report.get("test_results") or trajectory.get("test_results") or []
    for index, result in enumerate(test_results, 1):
        if isinstance(result, dict):
            add(
                "VERIFICATION",
                "evaluator",
                dict(result),
                timestamp=_time(result.get("created_at"), fallback=end_time),
                step_id=f"verification-{index}",
            )

    result = trajectory.get("result") if isinstance(trajectory.get("result"), dict) else {}
    final = add(
        "FINAL_RESULT",
        "agent",
        {
            "status": str(result.get("status") or "unknown"),
            "summary": str(result.get("summary") or ""),
            "diff_summary": trajectory.get("diff_summary") or {},
            "reward_report": report,
            "experience_candidates": trajectory.get("experience_candidates") or [],
        },
        timestamp=end_time,
    )
    add(
        "RUN_END",
        "agent",
        {"status": str(result.get("status") or "unknown")},
        timestamp=end_time,
        evidence_refs=(final.event_id,),
    )
    return events


def events_to_v1_trajectory(events: Iterable[AgentEvent | dict[str, Any]]) -> dict[str, Any]:
    """Build a V1-compatible read projection from normalized events."""

    normalized = [item if isinstance(item, AgentEvent) else AgentEvent.from_dict(item) for item in events]
    if not normalized:
        raise ValueError("cannot project an empty event stream")
    normalized.sort(key=lambda item: (item.timestamp, item.backend_sequence if item.backend_sequence is not None else 2**63))
    first = normalized[0]
    if any(item.trace_id != first.trace_id for item in normalized):
        raise ValueError("V1 projection accepts events from exactly one trace")
    start = next((item for item in normalized if item.type == "RUN_START"), first)
    final = next((item for item in reversed(normalized) if item.type == "FINAL_RESULT"), None)
    actions: list[dict[str, Any]] = []
    calls: dict[str, AgentEvent] = {}
    for event in normalized:
        if event.type == "TOOL_CALL" and event.tool_call_id:
            calls[event.tool_call_id] = event
        elif event.type == "TOOL_RESULT":
            call = calls.get(event.tool_call_id or "")
            payload = call.payload if call else {}
            result_payload = event.payload
            observation = result_payload.get("observation") if isinstance(result_payload.get("observation"), dict) else {}
            actions.append(
                {
                    "step": (call.step_id if call else event.step_id) or len(actions) + 1,
                    "action_type": str(payload.get("tool") or result_payload.get("tool") or "external_action"),
                    "input": payload.get("input") if isinstance(payload.get("input"), dict) else {},
                    "observation": observation,
                    "status": str(result_payload.get("status") or observation.get("status") or "unknown"),
                    "created_at": event.timestamp,
                }
            )
    start_payload = start.payload
    final_payload = final.payload if final else {}
    context = next((item for item in normalized if item.type == "CONTEXT_INJECT"), None)
    return {
        "schema": PRAXILE_TRAJECTORY_SCHEMA,
        "task_id": first.task_id,
        "trace_id": first.trace_id,
        "run_id": first.run_id,
        "user_task": str(start_payload.get("user_task") or "Imported normalized trace"),
        "start_time": start.timestamp,
        "end_time": normalized[-1].timestamp,
        "environment_snapshot": start_payload.get("environment_snapshot") or {},
        "loaded_assets": (context.payload.get("assets") if context else []) or [],
        "task_analysis": start_payload.get("task_analysis") or {},
        "plan": start_payload.get("plan") or [],
        "actions": actions,
        "artifacts": [dict(item.payload) for item in normalized if item.type == "ARTIFACT_CHANGE"],
        "diff_summary": final_payload.get("diff_summary") or {},
        "reward_report": final_payload.get("reward_report") or {},
        "experience_candidates": final_payload.get("experience_candidates") or [],
        "result": {
            "status": str(final_payload.get("status") or "unknown"),
            "summary": str(final_payload.get("summary") or ""),
        },
        "v2_projection": {
            "schema_version": AGENT_EVENT_SCHEMA_VERSION,
            "event_count": len(normalized),
            "source_of_truth": "normalized_event_stream",
        },
    }


def _deterministic_id(prefix: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}_{stable_hash(payload, 20)}"


def _time(value: Any, *, fallback: str | None = None) -> str:
    return str(value) if value else (fallback or utc_now())


def _cost(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None
