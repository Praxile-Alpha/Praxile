from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import Config
from .utils import stable_hash, utc_now


@dataclass(frozen=True)
class HarnessComponent:
    component_id: str
    title: str
    config_paths: tuple[str, ...] = ()
    asset_roots: tuple[str, ...] = ()


COMPONENTS: tuple[HarnessComponent, ...] = (
    HarnessComponent("prompts", "Agent prompts", ("task_analysis", "evolution"), ("rules/harness-rules",)),
    HarnessComponent("retrieval_policy", "Experience retrieval policy", ("retrieval", "search")),
    HarnessComponent("skills", "Project skill system", asset_roots=("skills",)),
    HarnessComponent("rules", "Harness and architecture rules", ("proposal_gate", "architecture_gate"), ("rules",)),
    HarnessComponent(
        "model_routing",
        "Model providers and role routing",
        ("model_roles", "model_providers", "routing"),
        ("rules/harness-rules/model-routing",),
    ),
    HarnessComponent("tool_policy", "Tool and shell safety policy", ("safety", "runtime"), ("rules/harness-rules",)),
    HarnessComponent("compression_profile", "Context compression profiles", ("context",)),
    HarnessComponent("stopping_policy", "Runtime stopping and retry policy", ("runtime", "checkpoint")),
    HarnessComponent("eval_policy", "Evaluation policy and cases", ("reward", "semantic_judges"), ("evals",)),
)

PROPOSAL_COMPONENTS = {
    "skill_create": "skills",
    "skill_update": "skills",
    "harness_rule": "rules",
    "harness_rule_create": "rules",
    "frozen_boundary": "rules",
    "architecture_gate": "rules",
    "routing": "model_routing",
    "eval_case": "eval_policy",
    "eval_checklist": "eval_policy",
    "proposal_gate_policy_update": "rules",
    "reward_policy": "eval_policy",
    "tool_policy_update": "tool_policy",
    "compression_profile_update": "compression_profile",
    "retrieval_policy_update": "retrieval_policy",
    "stopping_policy_update": "stopping_policy",
}


class HarnessComponentRegistry:
    def __init__(self, config: Config):
        self.config = config

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "frozen_outer_anchor": [
                "constitution.md",
                "rules/safety-policy.json",
                "evals/sealed/",
                "evals/scorers/",
                "architecture gate and proposal approval enforcement",
            ],
            "components": [self.describe(item.component_id) for item in COMPONENTS],
        }

    def describe(self, component_id: str) -> dict[str, Any]:
        component = next((item for item in COMPONENTS if item.component_id == component_id), None)
        if component is None:
            raise ValueError(f"Unknown harness component: {component_id}")
        state = self._component_state(component)
        return {
            "component_id": component.component_id,
            "title": component.title,
            "version": stable_hash(json.dumps(state, ensure_ascii=False, sort_keys=True), length=16),
            "config_paths": list(component.config_paths),
            "asset_roots": list(component.asset_roots),
            "state": state,
        }

    def component_change_for(self, proposal_type: str, changes: list[dict[str, Any]]) -> dict[str, Any] | None:
        component_id = PROPOSAL_COMPONENTS.get(str(proposal_type or ""))
        if not component_id:
            return None
        current = self.describe(component_id)
        normalized_changes = [
            {
                "path": str(item.get("path") or ""),
                "operation": str(item.get("operation") or "write"),
                "content_hash": stable_hash(
                    str(item.get("content") or json.dumps(item.get("metadata") or {}, sort_keys=True)),
                    length=16,
                ),
            }
            for item in changes
            if isinstance(item, dict)
        ]
        return {
            "component_id": component_id,
            "base_version": current["version"],
            "candidate_version": stable_hash(
                json.dumps({"base_version": current["version"], "changes": normalized_changes}, sort_keys=True),
                length=16,
            ),
            "change_count": len(normalized_changes),
        }

    def validate_proposal(self, proposal: dict[str, Any], *, verify_versions: bool = True) -> dict[str, Any] | None:
        expected = PROPOSAL_COMPONENTS.get(str(proposal.get("type") or ""))
        if not expected:
            return None
        change = proposal.get("component_change")
        if not isinstance(change, dict):
            raise ValueError(f"Harness proposal `{proposal.get('proposal_id')}` must declare component_change")
        required = {"component_id", "base_version", "candidate_version"}
        if not required <= set(change):
            raise ValueError("component_change requires component_id, base_version, and candidate_version")
        if change.get("component_id") != expected:
            raise ValueError(f"Proposal type `{proposal.get('type')}` must change exactly component `{expected}`")
        if isinstance(proposal.get("component_changes"), list):
            raise ValueError("Harness proposals may change exactly one registered component")
        for item in proposal.get("changes") or []:
            path = str(item.get("path") or "") if isinstance(item, dict) else ""
            if path == "constitution.md" or path == "rules/safety-policy.json" or path.startswith(("evals/sealed/", "evals/scorers/")):
                raise PermissionError(f"Harness proposal cannot modify frozen outer anchor: {path}")
            if expected == "model_routing" and not path.startswith("rules/harness-rules/model-routing/"):
                raise PermissionError("Routing candidates must stay inside rules/harness-rules/model-routing/")
            owner = _asset_component_owner(path)
            if owner and owner != expected:
                raise PermissionError(f"Candidate path `{path}` belongs to component `{owner}`, not `{expected}`")
        if verify_versions:
            computed = self.component_change_for(
                str(proposal.get("type") or ""),
                proposal.get("changes") if isinstance(proposal.get("changes"), list) else [],
            )
            if computed and (
                change.get("base_version") != computed.get("base_version")
                or change.get("candidate_version") != computed.get("candidate_version")
            ):
                raise ValueError("component_change versions do not match the active component and proposal changes")
        return change

    def _component_state(self, component: HarnessComponent) -> dict[str, Any]:
        config_state = {path: self.config.get(*path.split("."), default=None) for path in component.config_paths}
        assets: dict[str, str] = {}
        for root in component.asset_roots:
            directory = self.config.paths.state / root
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    rel = path.relative_to(self.config.paths.state).as_posix()
                    owner = _asset_component_owner(rel)
                    if owner and owner != component.component_id:
                        continue
                    try:
                        assets[rel] = stable_hash(path.read_text(encoding="utf-8"), length=16)
                    except UnicodeDecodeError:
                        continue
        return {"config": config_state, "assets": assets}


def is_harness_proposal(proposal: dict[str, Any]) -> bool:
    return str(proposal.get("type") or "") in PROPOSAL_COMPONENTS


def _asset_component_owner(path: str) -> str | None:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    prefixes = (
        ("rules/harness-rules/model-routing/", "model_routing"),
        ("rules/harness-rules/tool-policy/", "tool_policy"),
        ("rules/harness-rules/compression-profile/", "compression_profile"),
        ("rules/harness-rules/retrieval-policy/", "retrieval_policy"),
        ("rules/harness-rules/stopping-policy/", "stopping_policy"),
        ("rules/harness-rules/prompts/", "prompts"),
        ("rules/architecture-gates/", "rules"),
        ("rules/frozen-boundaries/", "rules"),
        ("rules/harness-rules/", "rules"),
        ("skills/", "skills"),
        ("evals/", "eval_policy"),
    )
    return next((owner for prefix, owner in prefixes if normalized.startswith(prefix)), None)
