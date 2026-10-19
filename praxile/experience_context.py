from __future__ import annotations

import json
import re
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .config import Config
from .eval.v2.activation import ContextActivationGate
from .eval.v2.representation import ExperienceRepresentationRouter
from .store import ExperienceStore
from .utils import shorten
from .experience_conditions import check_conditions


_MANDATORY_TYPES = {"frozen_boundary", "architecture_gate", "harness_rule", "rule"}
_STATE_DEPENDENCY = {"bugfix": 0.85, "refactor": 0.75, "architecture": 0.8, "ui": 0.6,
                     "feature": 0.5, "test": 0.55, "docs": 0.2}


@dataclass(frozen=True)
class _Repository:
    repo: str


@dataclass(frozen=True)
class _Task:
    task_id: str
    instruction: str
    metadata: Mapping[str, Any]
    repository: _Repository


@dataclass(frozen=True)
class _Candidate:
    candidate_id: str
    applies_to: Mapping[str, Any]
    representation_options: Mapping[str, str]
    representation_profile: Mapping[str, Any]


class RuntimeExperienceContext:
    """Project-local, deterministic projections for normal coding runs."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.state_root = config.paths.state.resolve()
        self.activation_gate = ContextActivationGate()
        self.router = ExperienceRepresentationRouter()

    def plan(
        self, task: str, task_id: str, analysis: Mapping[str, Any], retrieved: list[dict[str, Any]],
        *, observations: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        enabled = bool(self.config.get("context", "experience_representation_enabled", default=True))
        remaining = int(self.config.get("context", "experience_representation_budget_units", default=600))
        layer_remaining = dict(self.config.get("context", "experience_layer_budget_units",
                              default={"strategic": 150, "procedural": 300, "tool": 150}))
        task_type = str(analysis.get("task_type") or "feature")
        metadata = {
            "context_state_dependency": _STATE_DEPENDENCY.get(task_type, 0.5),
            "experience_intent": "failure" if task_type == "bugfix" else "procedure" if task_type in {"feature", "refactor", "test"} else "neutral",
        }
        view = _Task(task_id, task, metadata, _Repository(str(self.config.get("project", "name", default="project"))))
        selected: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for item in retrieved:
            path = str(item.get("path") or "")
            asset_type = str(item.get("type") or "")
            asset_path = self._asset_path(path)
            if asset_path is None:
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "asset file is missing or outside project state"})
                continue
            try:
                asset = self.store.get_asset(path)
            except (OSError, ValueError):
                asset = None
            if not asset or asset.get("status") != "active":
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "asset is not active"})
                continue
            conditions = {"status": "not_required", "checks": []}
            if asset_type not in _MANDATORY_TYPES:
                conditions = check_conditions(asset.get("activation_requirements"), observations)
                if conditions["status"] == "abstained":
                    decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                      "reason": conditions["reason"], "condition_check": conditions})
                    continue
            if asset_type in _MANDATORY_TYPES or not enabled:
                selected.append({**item, "activation_requirements": deepcopy(
                    asset.get("activation_requirements") if asset_type not in _MANDATORY_TYPES else None)})
                decisions.append({"path": path, "activation": "activated", "representation": "legacy",
                                  "condition_check": conditions,
                                  "reason": "mandatory control rule" if asset_type in _MANDATORY_TYPES else "representation routing disabled"})
                continue
            layer = asset.get("experience_layer")
            if layer is not None and (not isinstance(layer, str) or layer not in layer_remaining):
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "invalid experience layer metadata"})
                continue
            budget = min(remaining, layer_remaining[layer]) if layer else remaining
            if budget <= 0:
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "experience_layer": layer, "reason": "experience layer or total budget exhausted"})
                continue
            scope = asset.get("applies_to")
            if not isinstance(scope, Mapping):
                terms = [str(term) for term in item.get("matched_terms") or [] if str(term).strip()]
                scope = {"task_signals": terms[:5]}
            if not scope.get("task_signals") and not scope.get("repositories") and not scope.get("does_not_apply_when"):
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "retrieval supplied no task signal for semantic activation"})
                continue
            try:
                options = self._options(item, asset_path)
            except OSError:
                options = {}
            if not options:
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "asset has no safe representation"})
                continue
            if remaining <= 0:
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": "experience context budget is exhausted"})
                continue
            summary = options.get("summary_memory", "")
            longest = max(len(text) for text in options.values())
            compressibility = max(0.0, min(1.0, 1 - len(summary) / longest)) if summary else 0.3
            confidence = asset.get("confidence")
            density = 0.5 + (0.2 if asset.get("source_task_id") else 0)
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                density = min(density, max(0.0, min(1.0, float(confidence))))
            candidate = _Candidate(path, dict(scope), options, {
                "compressibility": compressibility,
                "evidence_density": density,
                "token_budget": budget,
            })
            try:
                activation = self.activation_gate.plan(candidate, [view])
                representation = self.router.plan(candidate, [view], activation)["decisions"][task_id]
            except (TypeError, ValueError) as exc:
                decisions.append({"path": path, "activation": "abstained", "representation": "none",
                                  "reason": f"invalid asset scope or routing profile: {exc}"})
                continue
            gate = activation["decisions"][task_id]
            kind = str(representation["selected"])
            decisions.append({
                "path": path,
                "activation": gate["status"],
                "representation": kind,
                "reason": representation["reason"],
                "matched_signals": gate["matched_signals"],
                "features": representation["features"],
                "estimated_context_units": representation["estimated_context_units"],
                "eligible": representation["eligible"],
                "experience_layer": layer,
                "layer_budget_before": layer_remaining.get(layer) if layer else None,
                "condition_check": conditions,
            })
            if kind == "none":
                continue
            chosen = {**item, "snippet": options[kind], "representation_kind": kind,
                      "experience_layer": layer,
                      "activation_requirements": deepcopy(asset.get("activation_requirements"))}
            selected.append(chosen)
            remaining -= int(representation["estimated_context_units"])
            if layer:
                layer_remaining[layer] = max(0, layer_remaining[layer] - int(representation["estimated_context_units"]))
            if remaining <= 0:
                remaining = 0
        return selected, decisions

    def _asset_path(self, relative: str) -> Path | None:
        try:
            path = (self.config.paths.root / relative).resolve(strict=True)
            path.relative_to(self.state_root)
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    def _options(self, item: Mapping[str, Any], path: Path) -> dict[str, str]:
        summary = str(item.get("snippet") or "").strip()
        options = {"summary_memory": shorten(summary, 1000)} if summary else {}
        kind = str(item.get("type") or "")
        if kind in {"skill", "failure_pattern"}:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                content = handle.read(1000).strip()
            if content:
                options["skill" if kind == "skill" else "failure_pattern"] = content
        elif kind == "trajectory_summary" and path.stat().st_size <= 1_000_000:
            try:
                episode = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return options
            if isinstance(episode, dict):
                result = episode.get("result")
                result_summary = result.get("summary") if isinstance(result, dict) else ""
                lines = [f"Task: {shorten(str(episode.get('user_task') or ''), 300)}",
                         f"Result: {shorten(str(result_summary or ''), 300)}"]
                actions = episode.get("actions")
                for action in (actions if isinstance(actions, list) else [])[:12]:
                    if isinstance(action, dict):
                        action_type = str(action.get("action_type") or "action")
                        status = str(action.get("status") or "unknown")
                        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", action_type) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,39}", status):
                            lines.append(f"- {action_type}: {status}")
                options["raw_episode"] = shorten("\n".join(lines), 1000)
        return {name: text for name, text in options.items() if text.strip()}
