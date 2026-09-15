from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..trace import AgentEvent
from .assets import SkillAsset
from .common import ControlPlaneSchemaError, canonical_json, require_mapping


SKILL_ACTIVATION_EXPERIMENT_SCHEMA_VERSION = "praxile.skill_activation_experiment.v1"
SUBAGENT_COMPARISON_SCHEMA_VERSION = "praxile.subagent_comparison.v1"
_INVARIANTS = ("task_set_digest", "adapter", "model", "evaluator")


@dataclass(frozen=True)
class ControlledArmMeasurement:
    arm_id: str
    task_set_digest: str
    adapter: str
    model: str
    evaluator: str
    task_count: int
    resolved_count: int
    input_tokens: int
    output_tokens: int
    tool_calls: int
    latency_ms: int
    cost: float | None

    def __post_init__(self) -> None:
        for name in ("arm_id", *_INVARIANTS):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ControlPlaneSchemaError(f"experiment arm {name} must be non-empty")
        for name in (
            "task_count",
            "resolved_count",
            "input_tokens",
            "output_tokens",
            "tool_calls",
            "latency_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ControlPlaneSchemaError(f"experiment arm {name} must be non-negative")
        if self.resolved_count > self.task_count:
            raise ControlPlaneSchemaError("resolved_count cannot exceed task_count")
        if self.cost is not None and self.cost < 0:
            raise ControlPlaneSchemaError("experiment arm cost must be non-negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlledArmMeasurement":
        require_mapping(value, "experiment arm")
        cost = value.get("cost")
        return cls(
            arm_id=str(value.get("arm_id") or ""),
            task_set_digest=str(value.get("task_set_digest") or ""),
            adapter=str(value.get("adapter") or ""),
            model=str(value.get("model") or ""),
            evaluator=str(value.get("evaluator") or ""),
            task_count=_integer(value.get("task_count"), "task_count"),
            resolved_count=_integer(value.get("resolved_count"), "resolved_count"),
            input_tokens=_integer(value.get("input_tokens"), "input_tokens"),
            output_tokens=_integer(value.get("output_tokens"), "output_tokens"),
            tool_calls=_integer(value.get("tool_calls"), "tool_calls"),
            latency_ms=_integer(value.get("latency_ms"), "latency_ms"),
            cost=float(cost) if cost is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "task_set_digest": self.task_set_digest,
            "adapter": self.adapter,
            "model": self.model,
            "evaluator": self.evaluator,
            "task_count": self.task_count,
            "resolved_count": self.resolved_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "latency_ms": self.latency_ms,
            "cost": self.cost,
        }


class SkillActivationExperiment:
    """Measure skill delivery and keep causal credit conservative."""

    def evaluate(
        self,
        skill: SkillAsset,
        baseline: ControlledArmMeasurement,
        candidate: ControlledArmMeasurement,
        candidate_events: Sequence[AgentEvent],
    ) -> dict[str, Any]:
        invariants = _check_invariants(baseline, candidate)
        injected = [
            event
            for event in candidate_events
            if event.type == "CONTEXT_INJECT"
            and any(
                isinstance(item, Mapping) and item.get("asset_id") == skill.meta.asset_id
                and str(item.get("asset_version") or "") == skill.meta.version
                for item in event.payload.get("items", [])
            )
        ]
        referenced = [
            event
            for event in candidate_events
            if event.type == "SKILL_REFERENCE"
            and event.payload.get("asset_id") == skill.meta.asset_id
            and str(event.payload.get("asset_version") or "") == skill.meta.version
        ]
        comparison = _compare(baseline, candidate)
        credited = bool(injected and referenced and comparison["decision"] == "improve")
        attribution = {
            "status": "credited" if credited else "abstained",
            "causal_credit": credited,
            "reason": (
                "The skill was injected, explicitly referenced, and the frozen comparison improved."
                if credited
                else "Causal credit requires injection, explicit reference, and an improving frozen comparison."
            ),
            "evidence_refs": [event.event_id for event in (*injected, *referenced)],
        }
        payload = {
            "schema_version": SKILL_ACTIVATION_EXPERIMENT_SCHEMA_VERSION,
            "skill": {"asset_id": skill.meta.asset_id, "version": skill.meta.version},
            "invariant_check": invariants,
            "baseline": baseline.to_dict(),
            "candidate": candidate.to_dict(),
            "activation": {
                "injected": bool(injected),
                "referenced": bool(referenced),
                "injection_event_ids": [event.event_id for event in injected],
                "reference_event_ids": [event.event_id for event in referenced],
            },
            "comparison": comparison,
            "attribution": attribution,
        }
        return {**payload, "result_digest": _digest(payload)}


class SubagentComparisonExperiment:
    """Measure a controlled no-subagent/subagent comparison from trace evidence."""

    def evaluate(
        self,
        baseline: ControlledArmMeasurement,
        candidate: ControlledArmMeasurement,
        candidate_events: Sequence[AgentEvent],
    ) -> dict[str, Any]:
        invariants = _check_invariants(baseline, candidate)
        starts = [event for event in candidate_events if event.type == "SUBAGENT_START"]
        ends = [event for event in candidate_events if event.type == "SUBAGENT_END"]
        merge_gates = [event for event in candidate_events if _is_merge_gate(event)]
        completed_delegations = []
        for start in starts:
            contract_id = str(start.payload.get("contract_id") or "")
            child_run_id = str(start.payload.get("child_run_id") or "")
            matching_end = next(
                (
                    event
                    for event in ends
                    if event.payload.get("contract_id") == contract_id
                    and event.payload.get("child_run_id") == child_run_id
                ),
                None,
            )
            matching_merge = next(
                (
                    event
                    for event in merge_gates
                    if event.payload.get("contract_id") == contract_id
                    and event.payload.get("child_run_id") == child_run_id
                    and str(event.payload.get("verifier_run_id") or "") != child_run_id
                ),
                None,
            )
            if contract_id and child_run_id and matching_end and matching_merge:
                completed_delegations.append(
                    {
                        "contract_id": contract_id,
                        "child_run_id": child_run_id,
                        "verifier_run_id": matching_merge.payload.get("verifier_run_id"),
                        "start_event_id": start.event_id,
                        "end_event_id": matching_end.event_id,
                        "merge_gate_event_id": matching_merge.event_id,
                    }
                )
        comparison = _compare(baseline, candidate)
        trace_complete = bool(completed_delegations)
        credited = trace_complete and comparison["decision"] == "improve"
        payload = {
            "schema_version": SUBAGENT_COMPARISON_SCHEMA_VERSION,
            "invariant_check": invariants,
            "baseline": baseline.to_dict(),
            "candidate": candidate.to_dict(),
            "delegation": {
                "trace_complete": trace_complete,
                "start_event_ids": [event.event_id for event in starts],
                "end_event_ids": [event.event_id for event in ends],
                "merge_gate_event_ids": [event.event_id for event in merge_gates],
                "completed_delegations": completed_delegations,
            },
            "comparison": comparison,
            "attribution": {
                "status": "credited" if credited else "abstained",
                "causal_credit": credited,
                "reason": (
                    "The isolated delegated arm has complete merge evidence and improved."
                    if credited
                    else "Credit requires a complete delegated trace, isolated merge evidence, and improvement."
                ),
            },
        }
        return {**payload, "result_digest": _digest(payload)}


def _check_invariants(
    baseline: ControlledArmMeasurement, candidate: ControlledArmMeasurement
) -> dict[str, Any]:
    violations = [
        field
        for field in _INVARIANTS
        if getattr(baseline, field) != getattr(candidate, field)
    ]
    if baseline.task_count != candidate.task_count:
        violations.append("task_count")
    return {"valid": not violations, "violations": violations, "frozen": list(_INVARIANTS)}


def _compare(
    baseline: ControlledArmMeasurement, candidate: ControlledArmMeasurement
) -> dict[str, Any]:
    invariants = _check_invariants(baseline, candidate)
    if not invariants["valid"]:
        decision = "invalid"
    elif candidate.resolved_count > baseline.resolved_count:
        decision = "improve"
    elif candidate.resolved_count < baseline.resolved_count:
        decision = "regress"
    else:
        decision = "inconclusive"
    return {
        "decision": decision,
        "resolved_delta": candidate.resolved_count - baseline.resolved_count,
        "input_token_delta": candidate.input_tokens - baseline.input_tokens,
        "output_token_delta": candidate.output_tokens - baseline.output_tokens,
        "tool_call_delta": candidate.tool_calls - baseline.tool_calls,
        "latency_ms_delta": candidate.latency_ms - baseline.latency_ms,
        "cost_delta": (
            round(candidate.cost - baseline.cost, 8)
            if candidate.cost is not None and baseline.cost is not None
            else None
        ),
    }


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_merge_gate(event: AgentEvent) -> bool:
    return (
        event.type == "CHECKPOINT"
        and event.payload.get("checkpoint_type") == "merge_gate"
        and event.payload.get("decision") == "merge"
        and bool(event.payload.get("verifier_run_id"))
    )


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlPlaneSchemaError(f"experiment arm {name} must be an integer")
    return value
