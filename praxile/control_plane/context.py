from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any, Mapping

from ..adapters import AdapterPolicy
from .common import ControlPlaneSchemaError, non_empty, require_mapping, safe_id, strict_bool, strict_int, strings


CONTEXT_POLICY_SCHEMA_VERSION = "praxile.context_policy.v2"
CONTEXT_SOURCES = frozenset(
    {
        "task_spec",
        "repository_map",
        "current_state",
        "recent_trajectory",
        "retrieved_experience",
        "retrieved_skill",
        "tool_result",
        "subagent_result",
        "artifact",
    }
)
CONTEXT_STAGES = frozenset({"exploration", "implementation", "verification"})
CONTEXT_POLICY_STATUSES = frozenset({"candidate", "approved", "active", "deprecated", "rolled_back"})


@dataclass(frozen=True)
class ContextSourceRule:
    source: str
    stages: tuple[str, ...]
    mode: str = "on_demand"
    max_tokens: int = 0
    required: bool = False
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in CONTEXT_SOURCES:
            raise ControlPlaneSchemaError(f"unsupported context source: {self.source!r}")
        if not self.stages or any(stage not in CONTEXT_STAGES for stage in self.stages):
            raise ControlPlaneSchemaError("context source stages must contain supported stages")
        if self.mode not in {"resident", "on_demand", "retrieved"}:
            raise ControlPlaneSchemaError(f"unsupported context source mode: {self.mode!r}")
        strict_int(self.max_tokens, "context source max_tokens")
        strict_bool(self.required, "context source required")
        require_mapping(self.filters, "context source filters")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextSourceRule":
        return cls(
            source=str(value.get("source") or ""),
            stages=strings(value.get("stages"), "context source stages", required=True),
            mode=str(value.get("mode") or "on_demand"),
            max_tokens=strict_int(value.get("max_tokens", 0), "context source max_tokens"),
            required=strict_bool(value.get("required", False), "context source required"),
            filters=dict(require_mapping(value.get("filters", {}), "context source filters")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "stages": list(self.stages),
            "mode": self.mode,
            "max_tokens": self.max_tokens,
            "required": self.required,
            "filters": dict(self.filters),
        }


@dataclass(frozen=True)
class StageBudget:
    stage: str
    token_limit: int
    tool_call_limit: int
    time_limit_seconds: int
    cost_limit: float | None = None

    def __post_init__(self) -> None:
        if self.stage not in CONTEXT_STAGES:
            raise ControlPlaneSchemaError(f"unsupported context stage: {self.stage!r}")
        for name in ("token_limit", "tool_call_limit", "time_limit_seconds"):
            strict_int(getattr(self, name), name, minimum=1)
        if self.cost_limit is not None and self.cost_limit < 0:
            raise ControlPlaneSchemaError("cost_limit must be non-negative")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StageBudget":
        raw_cost = value.get("cost_limit")
        return cls(
            stage=str(value.get("stage") or ""),
            token_limit=strict_int(value.get("token_limit"), "token_limit", minimum=1),
            tool_call_limit=strict_int(value.get("tool_call_limit"), "tool_call_limit", minimum=1),
            time_limit_seconds=strict_int(value.get("time_limit_seconds"), "time_limit_seconds", minimum=1),
            cost_limit=float(raw_cost) if raw_cost is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "stage": self.stage,
            "token_limit": self.token_limit,
            "tool_call_limit": self.tool_call_limit,
            "time_limit_seconds": self.time_limit_seconds,
        }
        if self.cost_limit is not None:
            result["cost_limit"] = self.cost_limit
        return result


@dataclass(frozen=True)
class ContextPolicy:
    policy_id: str
    version: str
    source_rules: tuple[ContextSourceRule, ...]
    stage_budgets: tuple[StageBudget, ...]
    status: str = "candidate"
    retrieval: Mapping[str, Any] = field(default_factory=dict)
    history: Mapping[str, Any] = field(default_factory=dict)
    repository: Mapping[str, Any] = field(default_factory=dict)
    experience: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTEXT_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTEXT_POLICY_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported context policy schema: {self.schema_version}")
        safe_id(self.policy_id, "context policy_id")
        non_empty(self.version, "context policy version")
        if self.status not in CONTEXT_POLICY_STATUSES:
            raise ControlPlaneSchemaError(f"unsupported context policy status: {self.status!r}")
        if not self.source_rules:
            raise ControlPlaneSchemaError("context policy requires source_rules")
        stages = [item.stage for item in self.stage_budgets]
        if set(stages) != CONTEXT_STAGES or len(stages) != len(set(stages)):
            raise ControlPlaneSchemaError("context policy requires exactly one budget for every stage")
        for name in ("retrieval", "history", "repository", "experience"):
            require_mapping(getattr(self, name), f"context policy {name}")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ContextPolicy":
        rules = value.get("source_rules")
        budgets = value.get("stage_budgets")
        if not isinstance(rules, list) or not isinstance(budgets, list):
            raise ControlPlaneSchemaError("source_rules and stage_budgets must be arrays")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            policy_id=str(value.get("policy_id") or ""),
            version=str(value.get("version") or ""),
            status=str(value.get("status") or ""),
            source_rules=tuple(ContextSourceRule.from_dict(item) for item in rules),
            stage_budgets=tuple(StageBudget.from_dict(item) for item in budgets),
            retrieval=dict(require_mapping(value.get("retrieval", {}), "context policy retrieval")),
            history=dict(require_mapping(value.get("history", {}), "context policy history")),
            repository=dict(require_mapping(value.get("repository", {}), "context policy repository")),
            experience=dict(require_mapping(value.get("experience", {}), "context policy experience")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "version": self.version,
            "status": self.status,
            "source_rules": [item.to_dict() for item in self.source_rules],
            "stage_budgets": [item.to_dict() for item in self.stage_budgets],
            "retrieval": dict(self.retrieval),
            "history": dict(self.history),
            "repository": dict(self.repository),
            "experience": dict(self.experience),
        }

    def compile(self, context_items: tuple[Mapping[str, Any], ...], *, evaluation: bool = False) -> AdapterPolicy:
        if self.status != "active" and not (evaluation and self.status == "candidate"):
            raise ControlPlaneSchemaError("only active policies may run outside an explicitly marked evaluation")
        allowed = {rule.source for rule in self.source_rules}
        required = {rule.source for rule in self.source_rules if rule.required}
        compiled: list[Mapping[str, Any]] = []
        measurements: list[Mapping[str, Any]] = []
        for index, item in enumerate(context_items):
            compiled_item = dict(item)
            raw_measurement = compiled_item.pop("_praxile_measurement", None)
            source = str(compiled_item.get("source") or "")
            if source not in allowed:
                raise ControlPlaneSchemaError(f"context item {index} uses undeclared source: {source!r}")
            measurement = _context_measurement(compiled_item, raw_measurement, index=index)
            compiled.append(compiled_item)
            measurements.append(measurement)
        present = {str(item.get("source") or "") for item in compiled}
        if required - present:
            raise ControlPlaneSchemaError(f"required context sources are missing: {sorted(required - present)}")
        budgets = {item.stage: item.to_dict() for item in self.stage_budgets}
        adapter_budgets: dict[str, Any] = {
            "stages": budgets,
            "wall_timeout_seconds": sum(item.time_limit_seconds for item in self.stage_budgets),
        }
        if all(item.cost_limit is not None for item in self.stage_budgets):
            adapter_budgets["max_cost"] = sum(float(item.cost_limit or 0.0) for item in self.stage_budgets)
        return AdapterPolicy(
            policy_id=self.policy_id,
            version=self.version,
            context=tuple(compiled),
            budgets=adapter_budgets,
            settings={
                "control_plane_schema": self.schema_version,
                "evaluation_only": self.status != "active",
                "retrieval": dict(self.retrieval),
                "history": dict(self.history),
                "repository": dict(self.repository),
                "experience": dict(self.experience),
                "source_rules": [item.to_dict() for item in self.source_rules],
                "context_source_measurements": measurements,
            },
        )


def _context_measurement(
    item: Mapping[str, Any], raw: Any, *, index: int
) -> dict[str, Any]:
    current_tokens = _estimated_tokens(item)
    if raw is None:
        measurement: Mapping[str, Any] = {}
    else:
        measurement = require_mapping(raw, f"context item {index} _praxile_measurement")
    decision = str(measurement.get("decision") or "passthrough")
    if decision not in {"passthrough", "compressed", "truncated"}:
        raise ControlPlaneSchemaError(
            f"context item {index} measurement decision is unsupported: {decision!r}"
        )
    input_tokens = strict_int(
        measurement.get("input_tokens", current_tokens),
        f"context item {index} input_tokens",
    )
    output_tokens = strict_int(
        measurement.get("output_tokens", current_tokens),
        f"context item {index} output_tokens",
    )
    if output_tokens > input_tokens:
        raise ControlPlaneSchemaError(
            f"context item {index} output_tokens cannot exceed input_tokens"
        )
    profile = str(measurement.get("compression_profile") or "none")
    reason = str(measurement.get("reason") or "context passed through without compression")
    return {
        "item_index": index,
        "source": str(item.get("source") or ""),
        "asset_id": str(item.get("asset_id") or "") or None,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_measurement": "reported" if raw is not None else "estimated",
        "compression_profile": profile,
        "decision": decision,
        "reason": reason,
    }


def _estimated_tokens(item: Mapping[str, Any]) -> int:
    payload = json.dumps(dict(item), ensure_ascii=False, sort_keys=True)
    return max(1, math.ceil(len(payload) / 4))
