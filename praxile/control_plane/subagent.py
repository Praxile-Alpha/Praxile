from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..trace import AgentEvent
from .common import ControlPlaneSchemaError, EvidenceRef, evidence_refs, non_empty, require_mapping, safe_id, strict_int, strings


DELEGATION_CONTRACT_SCHEMA_VERSION = "praxile.delegation_contract.v1"
MERGE_DECISION_SCHEMA_VERSION = "praxile.merge_decision.v1"
SUBAGENT_POLICY_SCHEMA_VERSION = "praxile.subagent_policy.v1"
CONTEXT_MODES = frozenset({"fresh", "isolated", "fork", "retrieved_only", "hybrid"})


@dataclass(frozen=True)
class SubagentPolicy:
    policy_id: str
    version: str
    trigger_conditions: tuple[str, ...]
    allowed_backends: tuple[str, ...]
    allowed_context_modes: tuple[str, ...]
    max_children: int
    max_parallel: int
    max_token_budget: int
    max_time_budget_seconds: int
    max_cost_budget: float
    require_isolated_verifier: bool = True
    status: str = "candidate"
    schema_version: str = SUBAGENT_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SUBAGENT_POLICY_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported subagent policy schema: {self.schema_version}")
        safe_id(self.policy_id, "subagent policy_id")
        non_empty(self.version, "subagent policy version")
        strings(self.trigger_conditions, "trigger_conditions", required=True)
        strings(self.allowed_backends, "allowed_backends", required=True)
        modes = strings(self.allowed_context_modes, "allowed_context_modes", required=True)
        if any(mode not in CONTEXT_MODES for mode in modes):
            raise ControlPlaneSchemaError("subagent policy contains an unsupported context mode")
        for name in ("max_children", "max_parallel", "max_token_budget", "max_time_budget_seconds"):
            strict_int(getattr(self, name), name, minimum=1)
        if self.max_parallel > self.max_children:
            raise ControlPlaneSchemaError("max_parallel cannot exceed max_children")
        if self.max_cost_budget < 0:
            raise ControlPlaneSchemaError("max_cost_budget must be non-negative")
        if not isinstance(self.require_isolated_verifier, bool):
            raise ControlPlaneSchemaError("require_isolated_verifier must be a boolean")
        if self.status not in {"candidate", "approved", "active", "deprecated", "rolled_back"}:
            raise ControlPlaneSchemaError(f"unsupported subagent policy status: {self.status!r}")

    def authorize(self, contract: "DelegationContract", *, evaluation: bool = False) -> None:
        if self.status != "active" and not (evaluation and self.status == "candidate"):
            raise ControlPlaneSchemaError("subagent policy is not active outside evaluation")
        if contract.backend not in self.allowed_backends:
            raise ControlPlaneSchemaError(f"delegation backend is not allowed by policy: {contract.backend}")
        if contract.context_mode not in self.allowed_context_modes:
            raise ControlPlaneSchemaError(f"delegation context mode is not allowed by policy: {contract.context_mode}")
        limits = (
            (contract.token_budget, self.max_token_budget, "token"),
            (contract.time_budget_seconds, self.max_time_budget_seconds, "time"),
            (contract.cost_budget, self.max_cost_budget, "cost"),
        )
        for requested, maximum, name in limits:
            if requested > maximum:
                raise ControlPlaneSchemaError(f"delegation {name} budget exceeds subagent policy")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SubagentPolicy":
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            policy_id=str(value.get("policy_id") or ""),
            version=str(value.get("version") or ""),
            trigger_conditions=strings(value.get("trigger_conditions"), "trigger_conditions", required=True),
            allowed_backends=strings(value.get("allowed_backends"), "allowed_backends", required=True),
            allowed_context_modes=strings(value.get("allowed_context_modes"), "allowed_context_modes", required=True),
            max_children=strict_int(value.get("max_children"), "max_children", minimum=1),
            max_parallel=strict_int(value.get("max_parallel"), "max_parallel", minimum=1),
            max_token_budget=strict_int(value.get("max_token_budget"), "max_token_budget", minimum=1),
            max_time_budget_seconds=strict_int(value.get("max_time_budget_seconds"), "max_time_budget_seconds", minimum=1),
            max_cost_budget=float(value.get("max_cost_budget", 0.0)),
            require_isolated_verifier=value.get("require_isolated_verifier", True),
            status=str(value.get("status") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "version": self.version,
            "trigger_conditions": list(self.trigger_conditions),
            "allowed_backends": list(self.allowed_backends),
            "allowed_context_modes": list(self.allowed_context_modes),
            "max_children": self.max_children,
            "max_parallel": self.max_parallel,
            "max_token_budget": self.max_token_budget,
            "max_time_budget_seconds": self.max_time_budget_seconds,
            "max_cost_budget": self.max_cost_budget,
            "require_isolated_verifier": self.require_isolated_verifier,
            "status": self.status,
        }


@dataclass(frozen=True)
class DelegationContract:
    contract_id: str
    objective: str
    scope: Mapping[str, Any]
    input_artifact_ids: tuple[str, ...]
    context_mode: str
    allowed_tools: tuple[str, ...]
    backend: str
    model: str
    token_budget: int
    time_budget_seconds: int
    cost_budget: float
    termination_criteria: tuple[str, ...]
    verification_criteria: tuple[str, ...]
    return_schema: Mapping[str, Any]
    schema_version: str = DELEGATION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DELEGATION_CONTRACT_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported delegation contract schema: {self.schema_version}")
        safe_id(self.contract_id, "contract_id")
        non_empty(self.objective, "delegation objective")
        require_mapping(self.scope, "delegation scope")
        strings(self.input_artifact_ids, "input_artifact_ids")
        if self.context_mode not in CONTEXT_MODES:
            raise ControlPlaneSchemaError(f"unsupported context_mode: {self.context_mode!r}")
        strings(self.allowed_tools, "allowed_tools", required=True)
        non_empty(self.backend, "delegation backend")
        non_empty(self.model, "delegation model")
        for name in ("token_budget", "time_budget_seconds"):
            strict_int(getattr(self, name), name, minimum=1)
        if self.cost_budget < 0:
            raise ControlPlaneSchemaError("cost_budget must be non-negative")
        strings(self.termination_criteria, "termination_criteria", required=True)
        strings(self.verification_criteria, "verification_criteria", required=True)
        schema = require_mapping(self.return_schema, "return_schema")
        if schema.get("type") != "object":
            raise ControlPlaneSchemaError("return_schema.type must be 'object'")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DelegationContract":
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            contract_id=str(value.get("contract_id") or ""),
            objective=str(value.get("objective") or ""),
            scope=dict(require_mapping(value.get("scope"), "delegation scope")),
            input_artifact_ids=strings(value.get("input_artifact_ids"), "input_artifact_ids"),
            context_mode=str(value.get("context_mode") or ""),
            allowed_tools=strings(value.get("allowed_tools"), "allowed_tools", required=True),
            backend=str(value.get("backend") or ""),
            model=str(value.get("model") or ""),
            token_budget=strict_int(value.get("token_budget"), "token_budget", minimum=1),
            time_budget_seconds=strict_int(value.get("time_budget_seconds"), "time_budget_seconds", minimum=1),
            cost_budget=float(value.get("cost_budget", 0)),
            termination_criteria=strings(value.get("termination_criteria"), "termination_criteria", required=True),
            verification_criteria=strings(value.get("verification_criteria"), "verification_criteria", required=True),
            return_schema=dict(require_mapping(value.get("return_schema"), "return_schema")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "objective": self.objective,
            "scope": dict(self.scope),
            "input_artifact_ids": list(self.input_artifact_ids),
            "context_mode": self.context_mode,
            "allowed_tools": list(self.allowed_tools),
            "backend": self.backend,
            "model": self.model,
            "token_budget": self.token_budget,
            "time_budget_seconds": self.time_budget_seconds,
            "cost_budget": self.cost_budget,
            "termination_criteria": list(self.termination_criteria),
            "verification_criteria": list(self.verification_criteria),
            "return_schema": dict(self.return_schema),
        }


@dataclass(frozen=True)
class MergeDecision:
    contract_id: str
    parent_run_id: str
    child_run_id: str
    decision: str
    verifier_run_id: str
    evidence: tuple[EvidenceRef, ...]
    rationale: str
    schema_version: str = MERGE_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MERGE_DECISION_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported merge decision schema: {self.schema_version}")
        for name in ("contract_id", "parent_run_id", "child_run_id", "verifier_run_id"):
            safe_id(getattr(self, name), name)
        if len({self.parent_run_id, self.child_run_id, self.verifier_run_id}) != 3:
            raise ControlPlaneSchemaError("parent, child, and verifier runs must be isolated")
        if self.decision not in {"merge", "reject", "retry"}:
            raise ControlPlaneSchemaError(f"unsupported merge decision: {self.decision!r}")
        evidence_refs(self.evidence)
        non_empty(self.rationale, "merge rationale")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MergeDecision":
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            contract_id=str(value.get("contract_id") or ""),
            parent_run_id=str(value.get("parent_run_id") or ""),
            child_run_id=str(value.get("child_run_id") or ""),
            verifier_run_id=str(value.get("verifier_run_id") or ""),
            decision=str(value.get("decision") or ""),
            evidence=evidence_refs(value.get("evidence", [])),
            rationale=str(value.get("rationale") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "verifier_run_id": self.verifier_run_id,
            "decision": self.decision,
            "evidence": [item.to_dict() for item in self.evidence],
            "rationale": self.rationale,
        }


def validate_delegation_trace(events: Sequence[AgentEvent], contract: DelegationContract, *, parent_run_id: str, child_run_id: str) -> None:
    parent_starts = [event for event in events if event.run_id == parent_run_id and event.type == "SUBAGENT_START"]
    parent_ends = [event for event in events if event.run_id == parent_run_id and event.type == "SUBAGENT_END"]
    child_events = [event for event in events if event.run_id == child_run_id]
    if not parent_starts or not parent_ends or not child_events:
        raise ControlPlaneSchemaError("delegation trace requires parent start/end and child events")
    if not any(event.payload.get("contract_id") == contract.contract_id and event.payload.get("child_run_id") == child_run_id for event in parent_starts):
        raise ControlPlaneSchemaError("SUBAGENT_START does not identify the delegation contract and child run")
    if any(event.parent_run_id != parent_run_id for event in child_events):
        raise ControlPlaneSchemaError("every child event must identify the parent run")
    if not any(event.type == "RUN_START" for event in child_events) or not any(event.type == "RUN_END" for event in child_events):
        raise ControlPlaneSchemaError("child run must have RUN_START and RUN_END events")
