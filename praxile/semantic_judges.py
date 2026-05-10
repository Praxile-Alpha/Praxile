from __future__ import annotations

import json
from typing import Any

from .config import Config
from .json_utils import parse_json_object
from .model import ModelRouter
from .utils import shorten


POSITIVE_ATTRIBUTIONS = {"weak_positive", "strong_positive"}
NEGATIVE_ATTRIBUTIONS = {"weak_negative", "harmful"}


def semantic_judges_enabled(config: Config) -> bool:
    return bool(config.get("semantic_judges", "enabled", default=False))


def semantic_judge_enabled(config: Config, name: str) -> bool:
    return semantic_judges_enabled(config) and bool(config.get("semantic_judges", name, "enabled", default=True))


def semantic_judge_role(config: Config, name: str, default: str) -> str:
    return str(config.get("semantic_judges", name, "role", default=default))


class AttributionJudge:
    def __init__(self, config: Config, router: ModelRouter):
        self.config = config
        self.router = router

    def judge_loaded_assets(self, trajectory: dict[str, Any], outcome: str, referenced_paths: list[str]) -> list[dict[str, Any]]:
        if not semantic_judge_enabled(self.config, "attribution_judge"):
            return []
        loaded = trajectory.get("loaded_assets") or []
        if not loaded or outcome not in {"success", "failed"}:
            return []
        threshold = _float_config(
            self.config.get(
                "semantic_judges",
                "attribution_judge",
                "only_for_loaded_assets_with_score_above",
                default=0.5,
            ),
            0.5,
        )
        max_assets = min(
            int(self.config.get("semantic_judges", "attribution_judge", "max_assets_per_run", default=4) or 4),
            int(self.config.get("semantic_judges", "max_calls_per_run", default=5) or 5),
        )
        referenced = set(referenced_paths or [])
        candidates = [
            item
            for item in loaded
            if _asset_path(item)
            and (
                _asset_path(item) in referenced
                or _score_value(item) >= threshold
                or bool(item.get("matched_terms"))
                or bool(item.get("matched_fields"))
            )
        ][:max(0, max_assets)]
        results: list[dict[str, Any]] = []
        for item in candidates:
            result = self._judge_one(trajectory, item, outcome, referenced)
            if result:
                results.append(result)
        return results

    def _judge_one(
        self,
        trajectory: dict[str, Any],
        asset: dict[str, Any],
        outcome: str,
        referenced: set[str],
    ) -> dict[str, Any] | None:
        path = _asset_path(asset)
        if not path:
            return None
        role = semantic_judge_role(self.config, "attribution_judge", "attribution_judge")
        payload = {
            "task": trajectory.get("user_task"),
            "outcome": outcome,
            "loaded_asset": {
                "path": path,
                "title": asset.get("title"),
                "kind": asset.get("kind") or asset.get("type"),
                "why_loaded": asset.get("why_loaded"),
                "matched_terms": asset.get("matched_terms") or [],
                "score": asset.get("final_score", asset.get("score")),
                "referenced_by_heuristic": path in referenced,
            },
            "actions_taken": _compact_actions(trajectory),
            "verification": _verification_commands(trajectory),
            "result": trajectory.get("result"),
        }
        try:
            response = self.router.chat(
                _messages(
                    "You are Praxile's local semantic attribution judge. Return exactly one JSON object. "
                    "You may judge whether a loaded experience asset actually influenced this run, but you must not "
                    "propose durable memory changes.",
                    (
                        "Return schema: {\"attribution_level\":\"none|loaded_only|referenced|weak_positive|weak_negative|"
                        "strong_positive|harmful|neutral|mixed|uncertain\",\"used_explicitly\":false,"
                        "\"confidence\":0.0,\"evidence\":[\"short evidence\"],"
                        "\"should_update_asset_outcome\":false,\"reason\":\"short reason\"}\n\n"
                        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                ),
                purpose=role,
                temperature=0,
                max_tokens=int(self.config.get("semantic_judges", "attribution_judge", "max_tokens", default=900)),
                timeout=int(self.config.get("semantic_judges", "attribution_judge", "timeout_seconds", default=12)),
            )
            parsed = parse_json_object(response.get("content", ""))
            level = _normalize_attribution_level(parsed.get("attribution_level"))
            if level not in {
                "none",
                "loaded_only",
                "referenced",
                "weak_positive",
                "weak_negative",
                "strong_positive",
                "harmful",
                "neutral",
                "mixed",
                "uncertain",
            }:
                level = "uncertain"
            evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []
            should_update = bool(parsed.get("should_update_asset_outcome"))
            if level in {"none", "loaded_only", "referenced", "neutral", "uncertain"}:
                should_update = False
            return {
                "path": path,
                "semantic_judge": {
                    "active": True,
                    "role": role,
                    "provider": response.get("provider"),
                    "model": response.get("model"),
                    "route": response.get("route", {}),
                    "latency_ms": response.get("latency_ms"),
                },
                "attribution_level": level,
                "used_explicitly": bool(parsed.get("used_explicitly")) or level in {"strong_positive", "harmful"},
                "referenced": path in referenced,
                "confidence": _score(parsed.get("confidence"), default=0.5),
                "evidence": [str(item) for item in evidence[:5]],
                "reason": str(parsed.get("reason") or ""),
                "should_update_asset_outcome": should_update,
            }
        except Exception as exc:
            return None


class PatternSemanticJudge:
    def __init__(self, config: Config, router: ModelRouter):
        self.config = config
        self.router = router
        self.calls = 0

    def should_judge(self, heuristic_score: float) -> bool:
        if not semantic_judge_enabled(self.config, "pattern_mining"):
            return False
        threshold = _float_config(
            self.config.get("semantic_judges", "pattern_mining", "only_after_heuristic_score", default=0.45),
            0.45,
        )
        max_calls = int(self.config.get("semantic_judges", "max_calls_per_mine_patterns", default=20) or 20)
        return heuristic_score >= threshold and self.calls < max_calls

    def judge_pair(self, left: dict[str, Any], right: dict[str, Any], heuristic_score: float) -> dict[str, Any] | None:
        if not self.should_judge(heuristic_score):
            return None
        self.calls += 1
        role = semantic_judge_role(self.config, "pattern_mining", "pattern_mining")
        payload = {"episode_a": _compact_episode(left), "episode_b": _compact_episode(right), "heuristic_score": heuristic_score}
        try:
            response = self.router.chat(
                _messages(
                    "You are Praxile's local semantic pattern judge. Return exactly one JSON object. "
                    "Judge whether two episodes share an underlying reusable project pattern.",
                    (
                        "Return schema: {\"same_underlying_pattern\":true,\"semantic_similarity\":0.0,"
                        "\"root_cause_similarity\":0.0,\"fix_strategy_similarity\":0.0,"
                        "\"verification_similarity\":0.0,\"should_merge\":true,"
                        "\"recommended_pattern_claim\":\"...\",\"reason\":\"...\"}\n\n"
                        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                ),
                purpose=role,
                temperature=0,
                max_tokens=int(self.config.get("semantic_judges", "pattern_mining", "max_tokens", default=900)),
                timeout=int(self.config.get("semantic_judges", "pattern_mining", "timeout_seconds", default=12)),
            )
            parsed = parse_json_object(response.get("content", ""))
            similarity = _score(parsed.get("semantic_similarity"), default=0.5)
            return {
                "active": True,
                "role": role,
                "provider": response.get("provider"),
                "model": response.get("model"),
                "route": response.get("route", {}),
                "same_underlying_pattern": bool(parsed.get("same_underlying_pattern")),
                "semantic_similarity": similarity,
                "root_cause_similarity": _score(parsed.get("root_cause_similarity"), default=similarity),
                "fix_strategy_similarity": _score(parsed.get("fix_strategy_similarity"), default=similarity),
                "verification_similarity": _score(parsed.get("verification_similarity"), default=similarity),
                "should_merge": bool(parsed.get("should_merge")),
                "recommended_pattern_claim": str(parsed.get("recommended_pattern_claim") or ""),
                "reason": str(parsed.get("reason") or ""),
            }
        except Exception as exc:
            return {"active": False, "role": role, "error": f"{exc.__class__.__name__}: {exc}"}


class CounterexampleSemanticChecker:
    def __init__(self, config: Config, router: ModelRouter):
        self.config = config
        self.router = router

    def check(self, hypothesis: dict[str, Any], candidate: dict[str, Any], *, heuristic_reason: str | None = None) -> dict[str, Any] | None:
        if not semantic_judge_enabled(self.config, "counterexample_checker"):
            return None
        role = semantic_judge_role(self.config, "counterexample_checker", "counterexample_checker")
        payload = {
            "hypothesis": {
                "claim": hypothesis.get("claim"),
                "applies_when": hypothesis.get("applies_when") or [],
                "does_not_apply_when": hypothesis.get("does_not_apply_when") or [],
                "failure_signatures": hypothesis.get("failure_signatures") or [],
                "fix_strategy": hypothesis.get("fix_strategy") or [],
                "affected_files": hypothesis.get("affected_files") or [],
            },
            "candidate_counterexample": _compact_episode(candidate),
            "heuristic_reason": heuristic_reason,
        }
        try:
            response = self.router.chat(
                _messages(
                    "You are Praxile's local counterexample semantic checker. Return exactly one JSON object. "
                    "Decide whether a similar episode should reduce confidence in the hypothesis.",
                    (
                        "Return schema: {\"is_counterexample\":true,"
                        "\"counterexample_type\":\"same_signature_different_root_cause\","
                        "\"confidence_delta\":-0.25,\"recommended_action\":\"inspect_or_edit\","
                        "\"reason\":\"short reason\"}\n\n"
                        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
                    ),
                ),
                purpose=role,
                temperature=0,
                max_tokens=int(self.config.get("semantic_judges", "counterexample_checker", "max_tokens", default=900)),
                timeout=int(
                    self.config.get("semantic_judges", "counterexample_checker", "timeout_seconds", default=12)
                ),
            )
            parsed = parse_json_object(response.get("content", ""))
            return {
                "active": True,
                "role": role,
                "provider": response.get("provider"),
                "model": response.get("model"),
                "route": response.get("route", {}),
                "is_counterexample": bool(parsed.get("is_counterexample")),
                "counterexample_type": str(parsed.get("counterexample_type") or "semantic_counterexample"),
                "confidence_delta": _negative_delta(parsed.get("confidence_delta"), default=-0.15),
                "recommended_action": _recommended_action(parsed.get("recommended_action")),
                "reason": str(parsed.get("reason") or ""),
            }
        except Exception as exc:
            return {"active": False, "role": role, "error": f"{exc.__class__.__name__}: {exc}"}


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _asset_path(asset: dict[str, Any]) -> str:
    return str(asset.get("path") or asset.get("asset_id") or "").strip()


def _score_value(item: dict[str, Any]) -> float:
    return _score(item.get("final_score", item.get("score")), default=0.0)


def _score(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 4)


def _float_config(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _negative_delta(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(-0.45, min(-0.01, parsed)), 4)


def _recommended_action(value: Any) -> str:
    text = str(value or "inspect_or_edit")
    return text if text in {"inspect_or_edit", "inspect", "reject_or_edit"} else "inspect_or_edit"


def _normalize_attribution_level(value: Any) -> str:
    text = str(value or "uncertain").strip().lower()
    aliases = {
        "medium_positive": "weak_positive",
        "medium_negative": "weak_negative",
        "strong_negative": "harmful",
        "negative": "weak_negative",
        "positive": "weak_positive",
        "none": "none",
    }
    return aliases.get(text, text)


def _compact_actions(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for action in (trajectory.get("actions") or [])[:20]:
        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        actions.append(
            {
                "step": action.get("step"),
                "type": action.get("action_type"),
                "input": action.get("input") or action.get("input_data"),
                "status": action.get("status"),
                "output": shorten(str(observation.get("output") or ""), 500),
            }
        )
    return actions


def _verification_commands(trajectory: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for action in trajectory.get("actions") or []:
        if action.get("action_type") not in {"run_test", "run_command"}:
            continue
        input_data = action.get("input") or action.get("input_data") or {}
        command = input_data.get("command") if isinstance(input_data, dict) else None
        if command:
            commands.append(str(command))
    return list(dict.fromkeys(commands))


def _compact_episode(episode: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": episode.get("episode_id"),
        "task_id": episode.get("task_id"),
        "category": episode.get("category"),
        "task_type": episode.get("task_type"),
        "failure_signature": episode.get("failure_signature"),
        "failure_signatures": episode.get("failure_signatures") or [],
        "affected_files": episode.get("affected_files") or episode.get("touched_files") or [],
        "fix_actions": episode.get("fix_actions") or [],
        "fix_strategy": episode.get("fix_strategy") or [],
        "verification_commands": episode.get("verification_commands") or episode.get("verification") or [],
        "root_cause": episode.get("root_cause"),
        "symptom": episode.get("symptom"),
        "outcome": episode.get("outcome") or episode.get("status"),
        "user_feedback": episode.get("user_feedback") or [],
    }
