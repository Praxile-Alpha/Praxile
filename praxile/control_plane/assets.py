from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .common import (
    LIFECYCLE_STATUSES,
    ControlPlaneSchemaError,
    EvidenceRef,
    confidence,
    evidence_refs,
    non_empty,
    require_mapping,
    safe_id,
    strings,
)


ASSET_META_SCHEMA_VERSION = "praxile.asset_meta.v2"
SKILL_ASSET_SCHEMA_VERSION = "praxile.skill_asset.v2"
ASSET_TYPES = frozenset({"rule", "skill", "failure_pattern", "eval_case", "context_policy", "recovery_strategy"})


@dataclass(frozen=True)
class AssetMeta:
    asset_id: str
    type: str
    source_evidence: tuple[EvidenceRef, ...]
    scope: Mapping[str, Any]
    applicable_conditions: tuple[str, ...]
    confidence: float
    version: str
    created_from_run: str
    status: str = "proposed"
    owner: str | None = None
    reviewer: str | None = None
    eval_result: Mapping[str, Any] = field(default_factory=dict)
    activation_history: tuple[Mapping[str, Any], ...] = ()
    schema_version: str = ASSET_META_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASSET_META_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported asset meta schema: {self.schema_version}")
        safe_id(self.asset_id, "asset_id")
        if self.type not in ASSET_TYPES:
            raise ControlPlaneSchemaError(f"unsupported asset type: {self.type!r}")
        evidence_refs(self.source_evidence)
        require_mapping(self.scope, "asset scope")
        strings(self.applicable_conditions, "applicable_conditions", required=True)
        confidence(self.confidence)
        non_empty(self.version, "asset version")
        safe_id(self.created_from_run, "created_from_run")
        if self.status not in LIFECYCLE_STATUSES:
            raise ControlPlaneSchemaError(f"unsupported asset status: {self.status!r}")
        require_mapping(self.eval_result, "eval_result")
        for item in self.activation_history:
            require_mapping(item, "activation_history item")
        if self.status == "active" and (not self.reviewer or not self.eval_result or not self.activation_history):
            raise ControlPlaneSchemaError("active assets require reviewer, eval_result, and activation_history")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssetMeta":
        require_mapping(value, "asset meta")
        history = value.get("activation_history", [])
        if not isinstance(history, list):
            raise ControlPlaneSchemaError("activation_history must be an array")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            asset_id=str(value.get("asset_id") or ""),
            type=str(value.get("type") or ""),
            source_evidence=evidence_refs(value.get("source_evidence", [])),
            scope=dict(require_mapping(value.get("scope"), "asset scope")),
            applicable_conditions=strings(value.get("applicable_conditions"), "applicable_conditions", required=True),
            confidence=confidence(value.get("confidence")),
            version=str(value.get("version") or ""),
            created_from_run=str(value.get("created_from_run") or ""),
            status=str(value.get("status") or ""),
            owner=str(value["owner"]) if value.get("owner") else None,
            reviewer=str(value["reviewer"]) if value.get("reviewer") else None,
            eval_result=dict(require_mapping(value.get("eval_result", {}), "eval_result")),
            activation_history=tuple(dict(require_mapping(item, "activation_history item")) for item in history),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "asset_id": self.asset_id,
            "type": self.type,
            "source_evidence": [item.to_dict() for item in self.source_evidence],
            "scope": dict(self.scope),
            "applicable_conditions": list(self.applicable_conditions),
            "confidence": self.confidence,
            "owner": self.owner,
            "reviewer": self.reviewer,
            "version": self.version,
            "created_from_run": self.created_from_run,
            "eval_result": dict(self.eval_result),
            "activation_history": [dict(item) for item in self.activation_history],
            "status": self.status,
        }


@dataclass(frozen=True)
class SkillAsset:
    meta: AssetMeta
    name: str
    input_schema: Mapping[str, Any]
    preconditions: tuple[str, ...]
    context_requirements: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    procedure: tuple[str, ...]
    verification_contract: tuple[str, ...]
    failure_modes: tuple[str, ...]
    eval_cases: tuple[str, ...]
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SKILL_ASSET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SKILL_ASSET_SCHEMA_VERSION:
            raise ControlPlaneSchemaError(f"unsupported skill schema: {self.schema_version}")
        if self.meta.type != "skill":
            raise ControlPlaneSchemaError("skill meta.type must be 'skill'")
        non_empty(self.name, "skill name")
        input_schema = require_mapping(self.input_schema, "skill input_schema")
        if input_schema.get("type") != "object":
            raise ControlPlaneSchemaError("skill input_schema.type must be 'object'")
        require_mapping(self.output_schema, "skill output_schema")
        strings(self.preconditions, "skill preconditions", required=True)
        strings(self.context_requirements, "skill context_requirements", required=True)
        strings(self.allowed_tools, "skill allowed_tools", required=True)
        strings(self.procedure, "skill procedure", required=True)
        strings(self.verification_contract, "skill verification_contract", required=True)
        strings(self.failure_modes, "skill failure_modes", required=True)
        strings(self.eval_cases, "skill eval_cases", required=True)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SkillAsset":
        require_mapping(value, "skill asset")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            meta=AssetMeta.from_dict(require_mapping(value.get("meta"), "skill meta")),
            name=str(value.get("name") or ""),
            input_schema=dict(require_mapping(value.get("input_schema"), "skill input_schema")),
            output_schema=dict(require_mapping(value.get("output_schema", {}), "skill output_schema")),
            preconditions=strings(value.get("preconditions"), "skill preconditions", required=True),
            context_requirements=strings(value.get("context_requirements"), "skill context_requirements", required=True),
            allowed_tools=strings(value.get("allowed_tools"), "skill allowed_tools", required=True),
            procedure=strings(value.get("procedure"), "skill procedure", required=True),
            verification_contract=strings(value.get("verification_contract"), "skill verification_contract", required=True),
            failure_modes=strings(value.get("failure_modes"), "skill failure_modes", required=True),
            eval_cases=strings(value.get("eval_cases"), "skill eval_cases", required=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "meta": self.meta.to_dict(),
            "name": self.name,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "preconditions": list(self.preconditions),
            "context_requirements": list(self.context_requirements),
            "allowed_tools": list(self.allowed_tools),
            "procedure": list(self.procedure),
            "verification_contract": list(self.verification_contract),
            "failure_modes": list(self.failure_modes),
            "eval_cases": list(self.eval_cases),
        }

    def context_item(self) -> dict[str, Any]:
        if self.meta.status != "active":
            raise ControlPlaneSchemaError("only active skills may be injected into a production run")
        return {
            "source": "retrieved_skill",
            "asset_id": self.meta.asset_id,
            "asset_version": self.meta.version,
            "name": self.name,
            "preconditions": list(self.preconditions),
            "context_requirements": list(self.context_requirements),
            "procedure": list(self.procedure),
            "verification_contract": list(self.verification_contract),
            "allowed_tools": list(self.allowed_tools),
            "evidence_refs": [item.canonical_ref for item in self.meta.source_evidence],
        }
