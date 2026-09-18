from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .schema import EvalSchemaError, EvalTaskSet, canonical_json


PROTOCOL_VERSION = "praxile.capability_protocol.v1"
_PRIVATE_METADATA_KEYS = frozenset({
    "answer", "expected", "rubric", "reference_patch", "test_patch",
    "fail_to_pass", "pass_to_pass", "private_metadata", "ground_truth",
    "evaluation", "evaluator", "scorer", "heldout_task_ids",
})
_PROXY_METRICS = frozenset({"token_count", "tool_calls", "latency_ms", "cost"})


def _record(value: Any, kind: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvalSchemaError(f"{kind} must be an object")
    unknown = set(value) - fields - {"schema_version"}
    if unknown:
        raise EvalSchemaError(f"{kind} has unknown fields: {sorted(unknown)}")
    if value.get("schema_version") != f"praxile.{kind}.v1":
        raise EvalSchemaError(f"unsupported {kind} schema_version")
    result = dict(value)
    for field in ("version",):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise EvalSchemaError(f"{kind}.{field} must be a non-empty string")
    canonical_json(result)
    return result


def _text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise EvalSchemaError(f"{field} must be a non-empty string")
    return item.strip()


def _strings(value: Mapping[str, Any], field: str, *, nonempty: bool = False) -> tuple[str, ...]:
    items = value.get(field)
    if not isinstance(items, list) or (nonempty and not items):
        raise EvalSchemaError(f"{field} must be {'a non-empty' if nonempty else 'an'} array")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise EvalSchemaError(f"{field} must contain non-empty strings")
    if len(items) != len(set(items)):
        raise EvalSchemaError(f"{field} contains duplicates")
    return tuple(items)


@dataclass(frozen=True)
class CapabilityGoal:
    goal_id: str
    version: str
    task_family: str
    objective: str
    success_criteria: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "CapabilityGoal":
        item = _record(value, "capability_goal", {"goal_id", "version", "task_family", "objective", "success_criteria"})
        return cls(_text(item, "goal_id"), _text(item, "version"), _text(item, "task_family"),
                   _text(item, "objective"), _strings(item, "success_criteria", nonempty=True))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "praxile.capability_goal.v1", "goal_id": self.goal_id,
                "version": self.version, "task_family": self.task_family, "objective": self.objective,
                "success_criteria": list(self.success_criteria)}


@dataclass(frozen=True)
class OperationalizationHypothesis:
    hypothesis_id: str
    version: str
    goal_id: str
    rationale: str
    proxy_metrics: tuple[str, ...]
    known_failure_modes: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "OperationalizationHypothesis":
        item = _record(value, "operationalization_hypothesis", {"hypothesis_id", "version", "goal_id", "rationale", "proxy_metrics", "known_failure_modes"})
        metrics = _strings(item, "proxy_metrics", nonempty=True)
        if set(metrics) - _PROXY_METRICS:
            raise EvalSchemaError(f"unsupported proxy metrics: {sorted(set(metrics) - _PROXY_METRICS)}")
        return cls(_text(item, "hypothesis_id"), _text(item, "version"), _text(item, "goal_id"),
                   _text(item, "rationale"), metrics, _strings(item, "known_failure_modes"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "praxile.operationalization_hypothesis.v1", "hypothesis_id": self.hypothesis_id,
                "version": self.version, "goal_id": self.goal_id, "rationale": self.rationale,
                "proxy_metrics": list(self.proxy_metrics), "known_failure_modes": list(self.known_failure_modes)}


@dataclass(frozen=True)
class EvaluationContract:
    contract_id: str
    version: str
    goal_id: str
    dataset_name: str
    split: str
    development_task_ids: tuple[str, ...]
    heldout_task_ids: tuple[str, ...]
    evaluator_owner: str
    evaluator_name: str
    primary_metric: str

    @classmethod
    def from_dict(cls, value: Any) -> "EvaluationContract":
        item = _record(value, "evaluation_contract", {"contract_id", "version", "goal_id", "dataset_name", "split", "development_task_ids", "heldout_task_ids", "evaluator_owner", "evaluator_name", "primary_metric"})
        result = cls(_text(item, "contract_id"), _text(item, "version"), _text(item, "goal_id"),
                     _text(item, "dataset_name"), _text(item, "split"), _strings(item, "development_task_ids"),
                     _strings(item, "heldout_task_ids", nonempty=True), _text(item, "evaluator_owner"),
                     _text(item, "evaluator_name"),
                     _text(item, "primary_metric"))
        if result.evaluator_owner != "control_plane":
            raise EvalSchemaError("held-out evaluator must have independent control-plane ownership")
        if result.primary_metric != "resolved":
            raise EvalSchemaError("P2-A terminal primary_metric must be objective 'resolved'")
        if set(result.development_task_ids) & set(result.heldout_task_ids):
            raise EvalSchemaError("development and held-out task IDs overlap")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "praxile.evaluation_contract.v1", "contract_id": self.contract_id,
                "version": self.version, "goal_id": self.goal_id, "dataset_name": self.dataset_name,
                "split": self.split, "development_task_ids": list(self.development_task_ids),
                "heldout_task_ids": list(self.heldout_task_ids), "evaluator_owner": self.evaluator_owner,
                "evaluator_name": self.evaluator_name,
                "primary_metric": self.primary_metric}


@dataclass(frozen=True)
class InformationBoundary:
    boundary_id: str
    version: str
    isolation_level: str
    forbidden_metadata_keys: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "InformationBoundary":
        item = _record(value, "information_boundary", {"boundary_id", "version", "isolation_level", "forbidden_metadata_keys"})
        level = _text(item, "isolation_level")
        if level != "logical_only":
            raise EvalSchemaError("P2-A local runner supports logical_only; OS-isolated eval is not implemented")
        return cls(_text(item, "boundary_id"), _text(item, "version"), level,
                   _strings(item, "forbidden_metadata_keys"))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "praxile.information_boundary.v1", "boundary_id": self.boundary_id,
                "version": self.version, "isolation_level": self.isolation_level,
                "forbidden_metadata_keys": list(self.forbidden_metadata_keys)}


@dataclass(frozen=True)
class TerminalSelectionRule:
    rule_id: str
    version: str
    minimum_heldout_gains: int
    maximum_heldout_regressions: int
    require_human_approval: bool

    @classmethod
    def from_dict(cls, value: Any) -> "TerminalSelectionRule":
        item = _record(value, "terminal_selection_rule", {"rule_id", "version", "minimum_heldout_gains", "maximum_heldout_regressions", "require_human_approval"})
        gains, regressions = item.get("minimum_heldout_gains"), item.get("maximum_heldout_regressions")
        if type(gains) is not int or gains < 1 or type(regressions) is not int or regressions < 0:
            raise EvalSchemaError("terminal selection requires positive minimum gains and nonnegative maximum regressions")
        if item.get("require_human_approval") is not True:
            raise EvalSchemaError("P2-A terminal selection cannot bypass human approval")
        return cls(_text(item, "rule_id"), _text(item, "version"), gains, regressions, True)

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "praxile.terminal_selection_rule.v1", "rule_id": self.rule_id,
                "version": self.version, "minimum_heldout_gains": self.minimum_heldout_gains,
                "maximum_heldout_regressions": self.maximum_heldout_regressions,
                "require_human_approval": self.require_human_approval}


@dataclass(frozen=True)
class CapabilityProtocol:
    goal: CapabilityGoal
    operationalization: OperationalizationHypothesis
    evaluation: EvaluationContract
    information_boundary: InformationBoundary
    terminal_selection_rule: TerminalSelectionRule

    @classmethod
    def load(cls, path: Path) -> "CapabilityProtocol":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, value: Any) -> "CapabilityProtocol":
        if not isinstance(value, Mapping) or value.get("schema_version") != PROTOCOL_VERSION:
            raise EvalSchemaError("unsupported capability protocol schema_version")
        fields = {"schema_version", "goal", "operationalization", "evaluation", "information_boundary", "terminal_selection_rule"}
        if set(value) != fields:
            raise EvalSchemaError(f"capability protocol fields differ: missing={sorted(fields - set(value))}, unknown={sorted(set(value) - fields)}")
        result = cls(CapabilityGoal.from_dict(value["goal"]),
                     OperationalizationHypothesis.from_dict(value["operationalization"]),
                     EvaluationContract.from_dict(value["evaluation"]),
                     InformationBoundary.from_dict(value["information_boundary"]),
                     TerminalSelectionRule.from_dict(value["terminal_selection_rule"]))
        if result.goal.goal_id != result.operationalization.goal_id or result.goal.goal_id != result.evaluation.goal_id:
            raise EvalSchemaError("capability protocol goal references disagree")
        if "heldout_resolution" not in result.goal.success_criteria:
            raise EvalSchemaError("P2-A goal must include heldout_resolution as a success criterion")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": PROTOCOL_VERSION, "goal": self.goal.to_dict(),
                "operationalization": self.operationalization.to_dict(), "evaluation": self.evaluation.to_dict(),
                "information_boundary": self.information_boundary.to_dict(),
                "terminal_selection_rule": self.terminal_selection_rule.to_dict()}

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def validate_run(self, task_set: EvalTaskSet, *, candidate_payload: Mapping[str, Any], evaluator_identity: Mapping[str, Any]) -> None:
        evaluation = self.evaluation
        if evaluator_identity.get("name") != evaluation.evaluator_name:
            raise EvalSchemaError("capability evaluator name differs from the actual evaluator identity")
        if (task_set.dataset_name, task_set.split) != (evaluation.dataset_name, evaluation.split):
            raise EvalSchemaError("capability evaluation dataset/split differs from task set")
        task_ids = {task.task_id for task in task_set.tasks}
        partitions = set(evaluation.development_task_ids) | set(evaluation.heldout_task_ids)
        if task_ids != partitions:
            raise EvalSchemaError(f"capability task partition mismatch: missing={sorted(task_ids - partitions)}, extra={sorted(partitions - task_ids)}")
        forbidden = _PRIVATE_METADATA_KEYS | {key.lower() for key in self.information_boundary.forbidden_metadata_keys}
        candidate_leaks = _leaking_keys(candidate_payload.get("context_item"), forbidden)
        if candidate_leaks:
            raise EvalSchemaError(f"candidate context contains evaluator fields: {sorted(candidate_leaks)}")
        for task in task_set.tasks:
            leaking = _leaking_keys(task.metadata, forbidden)
            if leaking:
                raise EvalSchemaError(f"task {task.task_id} metadata leaks evaluator fields: {sorted(leaking)}")
            private_values = [task.evaluation.reference_patch, task.evaluation.test_patch]
            candidate_text = canonical_json(candidate_payload)
            if any(value and len(value) >= 16 and value in candidate_text for value in private_values):
                raise EvalSchemaError(f"candidate includes private evaluator payload for {task.task_id}")

    def select_terminal(self, task_results: list[Mapping[str, Any]]) -> dict[str, Any]:
        by_id = {str(item.get("task_id")): item for item in task_results}
        heldout = set(self.evaluation.heldout_task_ids)
        if set(by_id) & heldout != heldout:
            raise EvalSchemaError("terminal selection lacks held-out task results")
        rows = [by_id[task_id] for task_id in self.evaluation.heldout_task_ids]
        development = [by_id[task_id] for task_id in self.evaluation.development_task_ids if task_id in by_id]
        proxy_fields = {"token_count": "token_delta", "tool_calls": "tool_call_delta",
                        "latency_ms": "latency_ms_delta", "cost": "cost_delta"}
        proxy_observations = {
            metric: (
                round(sum(float(row[proxy_fields[metric]]) for row in development), 8)
                if development and all(row.get(proxy_fields[metric]) is not None for row in development)
                else None
            )
            for metric in self.operationalization.proxy_metrics
        }
        gains = sum(row.get("transition") == "improved" for row in rows)
        regressions = sum(row.get("transition") == "regressed" for row in rows)
        unknown = sum(row.get("transition") == "unknown" for row in rows)
        if unknown:
            decision, reason = "inconclusive", "held-out objective result is missing"
        elif regressions > self.terminal_selection_rule.maximum_heldout_regressions:
            decision, reason = "blocked", "held-out regression limit exceeded"
        elif gains < self.terminal_selection_rule.minimum_heldout_gains:
            decision, reason = "inconclusive", "proxy improvement alone cannot satisfy the held-out goal"
        else:
            decision, reason = "human_review_required", "held-out objective threshold met; promotion still requires human approval"
        return {"decision": decision, "reason": reason, "heldout_count": len(rows),
                "heldout_gains": gains, "heldout_regressions": regressions, "heldout_unknown": unknown,
                "rule_id": self.terminal_selection_rule.rule_id,
                "rule_version": self.terminal_selection_rule.version,
                "proxy_metrics": list(self.operationalization.proxy_metrics),
                "development_proxy_deltas": proxy_observations,
                "automatic_promotion": False}


def _leaking_keys(value: Any, forbidden: frozenset[str] | set[str]) -> set[str]:
    if isinstance(value, Mapping):
        found = {str(key) for key in value if str(key).lower() in forbidden}
        for item in value.values():
            found.update(_leaking_keys(item, forbidden))
        return found
    if isinstance(value, (list, tuple)):
        found: set[str] = set()
        for item in value:
            found.update(_leaking_keys(item, forbidden))
        return found
    return set()
