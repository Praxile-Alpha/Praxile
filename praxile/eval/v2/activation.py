from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ...adapters import AdapterPolicy
from .schema import EvalSchemaError, EvalTask, canonical_json


CONTEXT_ACTIVATION_SCHEMA_VERSION = "praxile.context_activation.v1"
DEFAULT_SIGNAL_THRESHOLD = 0.6
DEFAULT_ANTI_SCOPE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ContextActivationDecision:
    task_id: str
    candidate_id: str
    activated: bool
    repository: str
    repository_matched: bool
    matched_signals: tuple[Mapping[str, Any], ...]
    matched_anti_scopes: tuple[Mapping[str, Any], ...]
    signal_threshold: float
    anti_scope_threshold: float
    reasons: tuple[str, ...]
    schema_version: str = CONTEXT_ACTIVATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "candidate_id": self.candidate_id,
            "status": "activated" if self.activated else "abstained",
            "activated": self.activated,
            "repository": self.repository,
            "repository_matched": self.repository_matched,
            "matched_signals": [dict(item) for item in self.matched_signals],
            "matched_anti_scopes": [dict(item) for item in self.matched_anti_scopes],
            "signal_threshold": self.signal_threshold,
            "anti_scope_threshold": self.anti_scope_threshold,
            "reasons": list(self.reasons),
            "evidence_basis": ["task.instruction", "task.metadata", "task.repository.repo"],
        }


class ContextActivationGate:
    """Deterministic, conservative activation from candidate scope and task semantics."""

    def evaluate(self, candidate: Any, task: EvalTask) -> ContextActivationDecision:
        scope = dict(candidate.applies_to)
        repositories = _strings(scope.get("repositories", ()), "applies_to.repositories")
        signals = _strings(scope.get("task_signals", ()), "applies_to.task_signals")
        anti_scopes = _strings(
            scope.get("does_not_apply_when", ()), "applies_to.does_not_apply_when"
        )
        signal_threshold = _threshold(
            scope.get("signal_threshold", DEFAULT_SIGNAL_THRESHOLD),
            "applies_to.signal_threshold",
        )
        anti_scope_threshold = _threshold(
            scope.get("anti_scope_threshold", DEFAULT_ANTI_SCOPE_THRESHOLD),
            "applies_to.anti_scope_threshold",
        )
        repository_matched = not repositories or task.repository.repo in repositories
        semantic_text = _task_semantic_text(task)
        matched_signals = tuple(
            match for signal in signals if (match := _match(signal, semantic_text)) and match["score"] >= signal_threshold
        )
        matched_anti_scopes = tuple(
            match
            for signal in anti_scopes
            if (match := _match(signal, semantic_text)) and match["score"] >= anti_scope_threshold
        )
        reasons: list[str] = []
        if not repository_matched:
            reasons.append("repository is outside applies_to.repositories")
        if signals and not matched_signals:
            reasons.append("no task signal reached the semantic activation threshold")
        if matched_anti_scopes:
            reasons.append("task matched does_not_apply_when")
        activated = repository_matched and (not signals or bool(matched_signals)) and not matched_anti_scopes
        if activated:
            reasons.append("candidate scope and semantic task signals matched")
        return ContextActivationDecision(
            task_id=task.task_id,
            candidate_id=str(candidate.candidate_id),
            activated=activated,
            repository=task.repository.repo,
            repository_matched=repository_matched,
            matched_signals=matched_signals,
            matched_anti_scopes=matched_anti_scopes,
            signal_threshold=signal_threshold,
            anti_scope_threshold=anti_scope_threshold,
            reasons=tuple(reasons),
        )

    def plan(self, candidate: Any, tasks: Sequence[EvalTask]) -> dict[str, Any]:
        decisions = {
            task.task_id: self.evaluate(candidate, task).to_dict() for task in tasks
        }
        payload = {
            "schema_version": CONTEXT_ACTIVATION_SCHEMA_VERSION,
            "mode": "deterministic_semantic_signals",
            "candidate_id": str(candidate.candidate_id),
            "decisions": decisions,
        }
        return {
            **payload,
            "decision_digest": "sha256:"
            + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
            "activated_count": sum(bool(item["activated"]) for item in decisions.values()),
            "abstained_count": sum(not bool(item["activated"]) for item in decisions.values()),
        }


def resolve_task_policy(
    policy: AdapterPolicy, task_id: str
) -> tuple[AdapterPolicy, dict[str, Any] | None]:
    context: list[Mapping[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    for item in policy.context:
        candidate_item = dict(item)
        raw_gate = candidate_item.pop("activation_gate", None)
        if not isinstance(raw_gate, Mapping):
            context.append(candidate_item)
            continue
        raw_decisions = raw_gate.get("decisions", {})
        decision = raw_decisions.get(task_id) if isinstance(raw_decisions, Mapping) else None
        if not isinstance(decision, Mapping):
            decisions.append(
                {
                    "schema_version": CONTEXT_ACTIVATION_SCHEMA_VERSION,
                    "task_id": task_id,
                    "candidate_id": candidate_item.get("candidate_id"),
                    "status": "abstained",
                    "activated": False,
                    "reasons": ["activation decision is missing for task"],
                }
            )
            continue
        normalized = dict(decision)
        decisions.append(normalized)
        if normalized.get("activated") is True:
            context.append(candidate_item)
    if not decisions:
        return policy, None
    effective = AdapterPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        context=tuple(context),
        budgets=dict(policy.budgets),
        settings=dict(policy.settings),
    )
    activation = {
        "schema_version": CONTEXT_ACTIVATION_SCHEMA_VERSION,
        "task_id": task_id,
        "status": "activated" if context else "abstained",
        "activated": bool(context),
        "decisions": decisions,
        "injected_context_items": len(context),
    }
    return effective, activation


def _task_semantic_text(task: EvalTask) -> str:
    metadata = " ".join(
        str(value) for value in task.metadata.values() if isinstance(value, (str, int, float))
    )
    return " ".join((task.instruction, metadata, task.repository.repo))


def _match(signal: str, text: str) -> dict[str, Any] | None:
    signal_terms = _terms(signal)
    if not signal_terms:
        return None
    text_terms = set(_terms(text))
    matched = sorted(set(signal_terms) & text_terms)
    normalized_signal = " ".join(signal_terms)
    normalized_text = " ".join(_terms(text))
    exact = normalized_signal in normalized_text
    score = 1.0 if exact else round(len(matched) / len(set(signal_terms)), 4)
    return {
        "signal": signal,
        "score": score,
        "exact_phrase": exact,
        "matched_terms": matched,
    }


def _terms(value: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", value.lower())
    return [_stem(term) for term in raw if len(term) > 1 or "\u4e00" <= term <= "\u9fff"]


def _stem(term: str) -> str:
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("ing"):
        return term[:-3]
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise EvalSchemaError(f"{name} must be an array of non-empty strings")
    return tuple(str(item).strip() for item in value)


def _threshold(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise EvalSchemaError(f"{name} must be between 0 and 1")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EvalSchemaError(f"{name} must be between 0 and 1") from exc
    if not 0.0 <= parsed <= 1.0:
        raise EvalSchemaError(f"{name} must be between 0 and 1")
    return parsed
