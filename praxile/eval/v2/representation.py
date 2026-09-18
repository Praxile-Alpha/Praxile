from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

from .schema import EvalSchemaError, EvalTask, canonical_json


REPRESENTATION_SCHEMA_VERSION = "praxile.experience_representation.v1"
ROUTER_POLICY_VERSION = "praxile.experience_router.v1"
REPRESENTATION_KINDS = ("raw_episode", "summary_memory", "skill", "failure_pattern")


def validate_representations(options: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    if not options:
        if profile:
            raise EvalSchemaError("representation_profile requires representation_options")
        return
    if set(options) - set(REPRESENTATION_KINDS):
        raise EvalSchemaError("unsupported experience representation")
    if any(not isinstance(content, str) or not content.strip() for content in options.values()):
        raise EvalSchemaError("representation options must contain non-empty text")
    required = {"compressibility", "evidence_density", "token_budget"}
    allowed = required | {"default_state_dependency"}
    if set(profile) - allowed or required - set(profile):
        raise EvalSchemaError("representation_profile requires compressibility, evidence_density, and token_budget")
    for field in ("compressibility", "evidence_density", "default_state_dependency"):
        if field in profile:
            _unit(profile[field], field)
    budget = profile["token_budget"]
    if type(budget) is not int or budget < 1:
        raise EvalSchemaError("representation token_budget must be a positive integer")


class ExperienceRepresentationRouter:
    """Choose a bounded context form only after semantic activation."""

    def plan(self, candidate: Any, tasks: Sequence[EvalTask], activation: Mapping[str, Any]) -> dict[str, Any]:
        options = dict(candidate.representation_options)
        profile = dict(candidate.representation_profile)
        validate_representations(options, profile)
        raw_decisions = activation.get("decisions", {})
        if not isinstance(raw_decisions, Mapping):
            raise EvalSchemaError("representation routing requires an activation decision map")
        decisions = {}
        for task in tasks:
            gate = raw_decisions.get(task.task_id)
            if not isinstance(gate, Mapping):
                raise EvalSchemaError(f"activation decision missing for {task.task_id}")
            decisions[task.task_id] = self._decide(task, gate, options, profile)
        payload = {
            "schema_version": REPRESENTATION_SCHEMA_VERSION,
            "policy_version": ROUTER_POLICY_VERSION,
            "candidate_id": candidate.candidate_id,
            "activation_digest": activation.get("decision_digest"),
            "decisions": decisions,
        }
        return {
            **payload,
            "decision_digest": "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
            "selected_count": sum(item["selected"] != "none" for item in decisions.values()),
            "none_count": sum(item["selected"] == "none" for item in decisions.values()),
        }

    @staticmethod
    def _decide(task: EvalTask, gate: Mapping[str, Any], options: Mapping[str, str], profile: Mapping[str, Any]) -> dict[str, Any]:
        if gate.get("activated") is not True:
            return _decision("none", "semantic activation abstained", {}, None)
        if not options:
            return _decision("legacy", "candidate has no representation options", {}, None)
        density = _unit(profile["evidence_density"], "evidence_density")
        compressibility = _unit(
            task.metadata.get("context_compressibility", profile["compressibility"]), "context_compressibility"
        )
        state_dependency = _unit(
            task.metadata.get("context_state_dependency", profile.get("default_state_dependency", 0.5)),
            "context_state_dependency",
        )
        budget = profile["token_budget"]
        task_budget = task.metadata.get("context_token_budget")
        if task_budget is not None:
            if type(task_budget) is not int or task_budget < 1:
                raise EvalSchemaError("context_token_budget must be a positive integer")
            budget = min(budget, task_budget)
        intent = task.metadata.get("experience_intent", "neutral")
        if intent not in {"neutral", "procedure", "failure"}:
            raise EvalSchemaError("experience_intent must be neutral, procedure, or failure")
        features = {
            "state_dependency": state_dependency,
            "compressibility": compressibility,
            "evidence_density": density,
            "context_budget_units": budget,
            "experience_intent": intent,
            "state_dependency_source": "task.metadata" if "context_state_dependency" in task.metadata else "profile_default",
            "compressibility_source": "task.metadata" if "context_compressibility" in task.metadata else "profile_default",
        }
        if density < 0.45:
            return _decision("none", "evidence density below routing threshold", features, None)
        eligible = {}
        for kind, content in options.items():
            cost = max(1, (len(content) + 3) // 4)
            if cost > budget:
                continue
            penalty = cost / budget
            scores = {
                "raw_episode": 2 * state_dependency + 2 * (1 - compressibility) + density - penalty,
                "summary_memory": 2 * compressibility + density + 0.5 * (1 - state_dependency) - penalty,
                "skill": 2 * (1 - state_dependency) + density + (1 if intent == "procedure" else 0) - penalty,
                "failure_pattern": state_dependency + density + (2.2 if intent == "failure" else 0) - penalty,
            }
            eligible[kind] = {"score": round(scores[kind], 6), "estimated_context_units": cost}
        if not eligible:
            return _decision("none", "no representation fits the token budget", features, None)
        selected = max(eligible, key=lambda kind: (eligible[kind]["score"], -eligible[kind]["estimated_context_units"], kind))
        return _decision(selected, "highest eligible deterministic representation score", features, eligible)


def _decision(selected: str, reason: str, features: Mapping[str, Any], eligible: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "schema_version": REPRESENTATION_SCHEMA_VERSION,
        "selected": selected,
        "reason": reason,
        "features": dict(features),
        "eligible": dict(eligible or {}),
        "estimated_context_units": (eligible or {}).get(selected, {}).get("estimated_context_units", 0),
    }


def _unit(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise EvalSchemaError(f"{field} must be a finite number between 0 and 1")
    return float(value)
