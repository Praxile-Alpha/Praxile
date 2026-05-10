from __future__ import annotations

from typing import Any


def detect_silent_failure_signals(trajectory: dict[str, Any], test_results: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    test_results = test_results or []
    result_status = trajectory.get("result", {}).get("status")
    actions = trajectory.get("actions") or []
    edits = [action for action in actions if action.get("action_type") == "edit_file" and action.get("status") == "success"]
    failures = [action for action in actions if action.get("status") in {"failure", "blocked"}]
    task_analysis = trajectory.get("task_analysis") or {}
    spec_context = trajectory.get("spec_context") or {}
    has_spec = bool(spec_context.get("spec_files"))
    detected_tests = trajectory.get("environment_snapshot", {}).get("tests_detected", []) or []
    edited_paths = _edited_paths(edits)
    top_levels = _top_levels(edited_paths)
    signals: list[dict[str, Any]] = []

    if result_status == "completed" and not test_results:
        risk = "medium" if detected_tests else "low"
        signals.append(
            _signal(
                "no_tests_but_completed",
                risk,
                "The run finished as completed without recorded verification commands.",
                affected_files=edited_paths,
            )
        )

    if len(top_levels) >= 4 and not has_spec:
        signals.append(
            _signal(
                "broad_diff_without_spec",
                "medium",
                f"The run edited files across {len(top_levels)} top-level areas without an attached spec.",
                affected_files=edited_paths,
            )
        )

    if len(edits) >= 4 and not spec_context.get("plan_files"):
        signals.append(
            _signal(
                "high_complexity_change_without_plan",
                "medium",
                f"The run recorded {len(edits)} edit actions without a plan.md or equivalent plan file.",
                affected_files=edited_paths,
            )
        )

    if edits and task_analysis.get("architecture_gate_required") and not _has_action(actions, "architecture_gate"):
        signals.append(
            _signal(
                "architecture_sensitive_change_without_gate",
                "high",
                "The task analysis marked the task architecture-sensitive, but edits were recorded without an architecture gate action.",
                affected_files=edited_paths,
            )
        )

    signatures = _failure_signatures(failures, test_results)
    if len(signatures) != len(set(signatures)) and signatures:
        signals.append(
            _signal(
                "repeated_failure_same_signature",
                "medium",
                "The run recorded repeated failure signatures that may need a failure-pattern proposal.",
                affected_files=edited_paths,
            )
        )

    loaded_assets = trajectory.get("loaded_assets") or []
    if loaded_assets and not trajectory.get("semantic_attributions"):
        signals.append(
            _signal(
                "loaded_asset_without_attribution",
                "low",
                "Experience assets were loaded, but no semantic attribution was recorded for their effect on the run.",
                affected_files=[],
            )
        )

    return _dedupe_signals(signals)


def apply_silent_failure_to_proposals(proposals: list[dict[str, Any]], signals: list[dict[str, Any]]) -> None:
    if not signals:
        return
    max_risk = _max_risk(signals)
    delta = -0.12 if max_risk == "high" else -0.08 if max_risk == "medium" else -0.03
    for proposal in proposals:
        proposal.setdefault("silent_failure_signals", signals)
        if proposal.get("type") in {"architecture_gate", "frozen_boundary"}:
            continue
        try:
            confidence = float(proposal.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        proposal["confidence"] = round(max(0.1, min(1.0, confidence + delta)), 3)
        proposal["confidence_level"] = _confidence_level(proposal["confidence"])
        if max_risk in {"medium", "high"}:
            proposal.setdefault("recommended_action_override", "inspect")


def _signal(
    signal_type: str,
    risk: str,
    reason: str,
    *,
    affected_files: list[str],
    recommended_action: str = "inspect",
) -> dict[str, Any]:
    return {
        "type": signal_type,
        "risk": risk,
        "reason": reason,
        "affected_files": affected_files[:20],
        "recommended_action": recommended_action,
    }


def _edited_paths(edits: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for action in edits:
        path = action.get("input", {}).get("path")
        if isinstance(path, str) and path:
            paths.append(path)
    return list(dict.fromkeys(paths))


def _top_levels(paths: list[str]) -> set[str]:
    result: set[str] = set()
    for path in paths:
        parts = [part for part in path.split("/") if part and part not in {".", ".."}]
        if parts:
            result.add(parts[0])
    return result


def _has_action(actions: list[dict[str, Any]], action_type: str) -> bool:
    return any(action.get("action_type") == action_type for action in actions)


def _failure_signatures(failures: list[dict[str, Any]], test_results: list[dict[str, Any]]) -> list[str]:
    signatures: list[str] = []
    for item in failures:
        output = str(item.get("observation", {}).get("output") or "")
        if output:
            signatures.append(_compact_signature(output))
    for item in test_results:
        if item.get("status") == "success":
            continue
        output = str(item.get("output") or item.get("data", {}).get("output") or "")
        if output:
            signatures.append(_compact_signature(output))
    return [item for item in signatures if item]


def _compact_signature(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        lower = line.lower()
        if "error" in lower or "failed" in lower or "exception" in lower or "traceback" in lower:
            return line[:180]
    return lines[0][:180] if lines else ""


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for signal in signals:
        key = str(signal.get("type"))
        if key in seen:
            continue
        seen.add(key)
        result.append(signal)
    return result


def _max_risk(signals: list[dict[str, Any]]) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return max((str(item.get("risk") or "low") for item in signals), key=lambda item: order.get(item, 0), default="low")


def _confidence_level(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.55:
        return "medium"
    return "low"

