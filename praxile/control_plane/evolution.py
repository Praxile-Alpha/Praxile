from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import ControlPlaneSchemaError, EvidenceRef, evidence_refs, non_empty, require_mapping, safe_id


HARNESS_CANDIDATE_SCHEMA_VERSION = "praxile.harness_candidate.v2"
LEGACY_HARNESS_CANDIDATE_SCHEMA_VERSION = "praxile.harness_candidate.v1"
CANDIDATE_EVALUATION_SCHEMA_VERSION = "praxile.candidate_evaluation.v1"
CANDIDATE_TYPES = frozenset({"prompt", "context_policy", "retrieval", "skill", "tool_policy", "model_routing", "subagent_policy", "recovery_policy"})
GATE_NAMES = ("evidence", "quality", "regression", "cost", "human", "rollback")


@dataclass(frozen=True)
class HarnessCandidate:
    candidate_id: str
    type: str
    component_key: str
    base_version: str
    candidate_version: str
    hypothesis: str
    source_evidence: tuple[EvidenceRef, ...]
    payload: Mapping[str, Any]
    risk: str = "medium"
    executor_profile: str = "default"
    task_family: str = "default"
    schema_version: str = HARNESS_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version not in {HARNESS_CANDIDATE_SCHEMA_VERSION, LEGACY_HARNESS_CANDIDATE_SCHEMA_VERSION}:
            raise ControlPlaneSchemaError(f"unsupported harness candidate schema: {self.schema_version}")
        safe_id(self.candidate_id, "candidate_id")
        if self.type not in CANDIDATE_TYPES:
            raise ControlPlaneSchemaError(f"unsupported candidate type: {self.type!r}")
        safe_id(self.component_key, "component_key")
        safe_id(self.executor_profile, "executor_profile")
        safe_id(self.task_family, "task_family")
        non_empty(self.base_version, "base_version")
        non_empty(self.candidate_version, "candidate_version")
        if self.base_version == self.candidate_version:
            raise ControlPlaneSchemaError("candidate_version must differ from base_version")
        non_empty(self.hypothesis, "candidate hypothesis")
        evidence_refs(self.source_evidence)
        if not require_mapping(self.payload, "candidate payload"):
            raise ControlPlaneSchemaError("candidate payload must not be empty")
        if self.risk not in {"low", "medium", "high"}:
            raise ControlPlaneSchemaError(f"unsupported candidate risk: {self.risk!r}")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HarnessCandidate":
        schema = str(value.get("schema_version") or "")
        if schema == LEGACY_HARNESS_CANDIDATE_SCHEMA_VERSION:
            schema = HARNESS_CANDIDATE_SCHEMA_VERSION
        return cls(
            schema_version=schema,
            candidate_id=str(value.get("candidate_id") or ""),
            type=str(value.get("type") or ""),
            component_key=str(value.get("component_key") or ""),
            base_version=str(value.get("base_version") or ""),
            candidate_version=str(value.get("candidate_version") or ""),
            hypothesis=str(value.get("hypothesis") or ""),
            source_evidence=evidence_refs(value.get("source_evidence", [])),
            payload=dict(require_mapping(value.get("payload"), "candidate payload")),
            risk=str(value.get("risk") or ""),
            executor_profile=str(value.get("executor_profile") or "default"),
            task_family=str(value.get("task_family") or "default"),
        )

    @property
    def promotion_key(self) -> str:
        return promotion_key(self.component_key, self.executor_profile, self.task_family)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "type": self.type,
            "component_key": self.component_key,
            "base_version": self.base_version,
            "candidate_version": self.candidate_version,
            "hypothesis": self.hypothesis,
            "source_evidence": [item.to_dict() for item in self.source_evidence],
            "payload": dict(self.payload),
            "risk": self.risk,
            "executor_profile": self.executor_profile,
            "task_family": self.task_family,
            "promotion_key": self.promotion_key,
        }


def promotion_key(component_key: str, executor_profile: str, task_family: str) -> str:
    safe_id(component_key, "component_key")
    safe_id(executor_profile, "executor_profile")
    safe_id(task_family, "task_family")
    return f"{component_key}::{executor_profile}::{task_family}"


@dataclass(frozen=True)
class GateResult:
    gate: str
    passed: bool
    evidence: tuple[EvidenceRef, ...]
    summary: str

    def __post_init__(self) -> None:
        if self.gate not in GATE_NAMES:
            raise ControlPlaneSchemaError(f"unsupported promotion gate: {self.gate!r}")
        if not isinstance(self.passed, bool):
            raise ControlPlaneSchemaError("gate passed must be a boolean")
        evidence_refs(self.evidence)
        non_empty(self.summary, "gate summary")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GateResult":
        return cls(
            gate=str(value.get("gate") or ""),
            passed=value.get("passed"),
            evidence=evidence_refs(value.get("evidence", [])),
            summary=str(value.get("summary") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "passed": self.passed, "evidence": [item.to_dict() for item in self.evidence], "summary": self.summary}


@dataclass(frozen=True)
class CandidateEvaluation:
    candidate_id: str
    baseline_ref: str
    candidate_eval_ref: str
    gates: tuple[GateResult, ...]
    decision: str
    reviewer: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    rollback_target: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CANDIDATE_EVALUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CANDIDATE_EVALUATION_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported candidate evaluation schema: {self.schema_version}")
        safe_id(self.candidate_id, "candidate_id")
        non_empty(self.baseline_ref, "baseline_ref")
        non_empty(self.candidate_eval_ref, "candidate_eval_ref")
        names = [item.gate for item in self.gates]
        if tuple(sorted(names)) != tuple(sorted(GATE_NAMES)) or len(names) != len(set(names)):
            raise ControlPlaneSchemaError("candidate evaluation requires exactly one result for every promotion gate")
        if self.decision not in {"promote", "reject", "abstain"}:
            raise ControlPlaneSchemaError(f"unsupported evaluation decision: {self.decision!r}")
        non_empty(self.reviewer, "evaluation reviewer")
        require_mapping(self.metrics, "evaluation metrics")
        require_mapping(self.rollback_target, "rollback_target")
        if self.decision == "promote" and (not all(item.passed for item in self.gates) or not self.rollback_target):
            raise ControlPlaneSchemaError("promotion requires all gates to pass and a rollback_target")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateEvaluation":
        raw_gates = value.get("gates")
        if not isinstance(raw_gates, list):
            raise ControlPlaneSchemaError("gates must be an array")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            candidate_id=str(value.get("candidate_id") or ""),
            baseline_ref=str(value.get("baseline_ref") or ""),
            candidate_eval_ref=str(value.get("candidate_eval_ref") or ""),
            gates=tuple(GateResult.from_dict(item) for item in raw_gates),
            decision=str(value.get("decision") or ""),
            reviewer=str(value.get("reviewer") or ""),
            metrics=dict(require_mapping(value.get("metrics", {}), "evaluation metrics")),
            rollback_target=dict(require_mapping(value.get("rollback_target", {}), "rollback_target")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "baseline_ref": self.baseline_ref,
            "candidate_eval_ref": self.candidate_eval_ref,
            "gates": [item.to_dict() for item in self.gates],
            "decision": self.decision,
            "reviewer": self.reviewer,
            "metrics": dict(self.metrics),
            "rollback_target": dict(self.rollback_target),
        }
