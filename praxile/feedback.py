from __future__ import annotations

import json
import re
from typing import Any

from .json_utils import parse_json_object
from .semantic_judges import semantic_judge_enabled, semantic_judge_role
from .utils import new_id, utc_now


POSITIVE_TERMS = {
    "good",
    "great",
    "nice",
    "accurate",
    "helpful",
    "works",
    "useful",
    "干得好",
    "不错",
    "准确",
    "有用",
    "很好",
    "可以",
}
NEGATIVE_TERMS = {
    "wrong",
    "bad",
    "unhelpful",
    "harmful",
    "misleading",
    "too generic",
    "incorrect",
    "不对",
    "错了",
    "误导",
    "太泛",
    "没价值",
    "不要这么记",
    "理解错",
}


def build_feedback(
    *,
    target_type: str,
    target_id: str,
    raw_text: str = "",
    sentiment: str | None = None,
    feedback_type: str | None = None,
    strength: float | None = None,
    effect: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_sentiment = sentiment or classify_sentiment(raw_text)
    normalized_strength = _clamp(strength if strength is not None else infer_strength(raw_text, normalized_sentiment))
    interpreted_reward = interpreted_reward_delta(normalized_sentiment, normalized_strength)
    return {
        "feedback_id": new_id("fb"),
        "target_type": normalize_target_type(target_type),
        "target_id": target_id,
        "sentiment": normalized_sentiment,
        "strength": normalized_strength,
        "feedback_type": feedback_type or infer_feedback_type(normalize_target_type(target_type), raw_text, normalized_sentiment),
        "raw_text": raw_text,
        "interpreted_reward": interpreted_reward,
        "effect": effect or default_effect(normalize_target_type(target_type), normalized_sentiment, raw_text),
        "created_at": utc_now(),
        "schema_version": 1,
    }


def classify_sentiment(raw_text: str) -> str:
    lower = raw_text.lower()
    if any(term in lower for term in NEGATIVE_TERMS):
        return "negative"
    if any(term in lower for term in POSITIVE_TERMS):
        return "positive"
    return "neutral"


def infer_strength(raw_text: str, sentiment: str) -> float:
    if sentiment == "neutral":
        return 0.0
    lower = raw_text.lower()
    if any(term in lower for term in {"very", "really", "非常", "很", "准确", "误导"}):
        return 0.9
    return 0.7


def infer_feedback_type(target_type: str, raw_text: str, sentiment: str) -> str:
    lower = raw_text.lower()
    if target_type == "run":
        return "run_failure" if sentiment == "negative" else "run_satisfaction"
    if target_type == "proposal":
        if "太泛" in lower or "too generic" in lower:
            return "proposal_too_generic"
        return "proposal_quality"
    if target_type == "asset":
        if "误导" in lower or "misleading" in lower:
            return "rule_misleading"
        return "asset_harmful" if sentiment == "negative" else "asset_helpful"
    if target_type == "pattern":
        return "pattern_counterexample" if sentiment == "negative" else "pattern_helpful"
    return "general_feedback"


def default_effect(target_type: str, sentiment: str, raw_text: str) -> dict[str, Any]:
    if sentiment == "positive":
        if target_type == "asset":
            return {"increase_positive_outcome": True}
        if target_type == "proposal":
            return {"raise_confidence": True}
        return {"increase_run_reward": True}
    if sentiment == "negative":
        if target_type == "asset":
            return {"increase_negative_outcome": True, "requires_governed_rewrite_or_deprecation": True}
        if target_type == "proposal":
            return {
                "lower_confidence": True,
                "recommended_action": "reject_or_edit",
                "suppress_similar": "太泛" in raw_text or "too generic" in raw_text.lower(),
            }
        return {"decrease_run_reward": True}
    return {}


def interpreted_reward_delta(sentiment: str, strength: float) -> float:
    if sentiment == "positive":
        return round(0.25 * _clamp(strength), 3)
    if sentiment == "negative":
        return round(-0.35 * _clamp(strength), 3)
    return 0.0


def feedback_reward(events: list[dict[str, Any]]) -> dict[str, Any]:
    positive = sum(1 for item in events if item.get("sentiment") == "positive")
    negative = sum(1 for item in events if item.get("sentiment") == "negative")
    neutral = sum(1 for item in events if item.get("sentiment") == "neutral")
    delta = sum(float(item.get("interpreted_reward") or 0.0) for item in events)
    score = _clamp(0.5 + delta)
    return {
        "enabled": True,
        "active": bool(events),
        "score": round(score, 3) if events else 0.0,
        "baseline": 0.5,
        "net_interpreted_reward": round(delta, 3),
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
        "events": [
            {
                "feedback_id": item.get("feedback_id"),
                "target_type": item.get("target_type"),
                "target_id": item.get("target_id"),
                "sentiment": item.get("sentiment"),
                "strength": item.get("strength"),
                "feedback_type": item.get("feedback_type"),
                "interpreted_reward": item.get("interpreted_reward"),
                "created_at": item.get("created_at"),
            }
            for item in events
        ],
    }


def extract_feedback_intents(raw_text: str) -> list[dict[str, Any]]:
    """
    Lightweight rule-first feedback extraction for short CLI/chat feedback.

    This intentionally stays conservative: ambiguous durable-asset negative
    feedback is marked as requiring confirmation instead of being applied
    silently.
    """
    text = str(raw_text or "").strip()
    if not text:
        return []
    chunks = [chunk.strip() for chunk in re.split(r"[;；。]\s*|\n+", text) if chunk.strip()]
    if not chunks:
        chunks = [text]
    intents: list[dict[str, Any]] = []
    for chunk in chunks:
        lowered = chunk.lower()
        sentiment = classify_sentiment(chunk)
        if _mentions_nth_proposal(lowered):
            intents.append(
                {
                    "target_type": "proposal",
                    "target_hint": _mentions_nth_proposal(lowered),
                    "sentiment": sentiment if sentiment != "neutral" else "negative",
                    "feedback_type": infer_feedback_type("proposal", chunk, sentiment if sentiment != "neutral" else "negative"),
                    "raw_text": chunk,
                    "strength": infer_strength(chunk, sentiment if sentiment != "neutral" else "negative"),
                    "effect": {"suppress_asset_write": sentiment == "negative" or "不要" in chunk},
                }
            )
            continue
        if "proposal" in lowered or "提案" in chunk or "经验" in chunk:
            intents.append(
                {
                    "target_type": "proposal",
                    "target_hint": "latest",
                    "sentiment": sentiment,
                    "feedback_type": infer_feedback_type("proposal", chunk, sentiment),
                    "raw_text": chunk,
                    "strength": infer_strength(chunk, sentiment),
                }
            )
            continue
        if "规则" in chunk or "rule" in lowered or "skill" in lowered or "memory" in lowered:
            intents.append(
                {
                    "target_type": "asset",
                    "target_hint": "recent_loaded_asset",
                    "sentiment": sentiment,
                    "feedback_type": infer_feedback_type("asset", chunk, sentiment),
                    "raw_text": chunk,
                    "strength": infer_strength(chunk, sentiment),
                    "requires_confirmation": sentiment == "negative",
                }
            )
            continue
        intents.append(
            {
                "target_type": "run",
                "target_hint": "latest",
                "sentiment": sentiment,
                "feedback_type": infer_feedback_type("run", chunk, sentiment),
                "raw_text": chunk,
                "strength": infer_strength(chunk, sentiment),
            }
        )
    return intents


class FeedbackSemanticClassifier:
    def __init__(self, config: Any, router: Any):
        self.config = config
        self.router = router

    def classify(self, raw_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        text = str(raw_text or "").strip()
        if not text:
            return {"active": False, "feedback_events": [], "fallback_reason": "empty_feedback"}
        rule_events = extract_feedback_intents(text)
        if not semantic_judge_enabled(self.config, "feedback_classifier"):
            return {"active": False, "feedback_events": rule_events, "fallback_reason": "semantic_judge_disabled"}
        complex_only = bool(
            self.config.get(
                "semantic_judges",
                "feedback_classifier",
                "use_for_complex_feedback_only",
                default=True,
            )
        )
        if complex_only and not _complex_feedback(text):
            return {"active": False, "feedback_events": rule_events, "fallback_reason": "simple_feedback_rule_handled"}
        role = semantic_judge_role(self.config, "feedback_classifier", "feedback_classifier")
        payload = {
            "user_text": text,
            "conversation_context": context or {},
            "rule_first_candidates": rule_events,
        }
        try:
            response = self.router.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are Praxile's local feedback semantic classifier. Return exactly one JSON object. "
                            "Split feedback into targetable events. Do not decide durable writes; mark risky durable "
                            "asset or proposal feedback as requiring confirmation."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Return schema: {\"feedback_events\":[{\"target_type\":\"run|proposal|asset|pattern\","
                            "\"target_ref\":\"latest|second|third|nth:2|asset path|pattern id\","
                            "\"sentiment\":\"positive|negative|neutral\","
                            "\"feedback_type\":\"satisfaction|do_not_persist|too_generic|asset_harmful|pattern_counterexample\","
                            "\"strength\":0.0,\"requires_confirmation\":false,\"reason\":\"short reason\"}]}\n\n"
                            f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
                        ),
                    },
                ],
                purpose=role,
                temperature=0,
                max_tokens=int(self.config.get("semantic_judges", "feedback_classifier", "max_tokens", default=800)),
                timeout=int(self.config.get("semantic_judges", "feedback_classifier", "timeout_seconds", default=12)),
            )
            parsed = parse_json_object(response.get("content", ""))
            events = _normalize_semantic_feedback_events(parsed.get("feedback_events"), raw_text=text)
            if not events:
                return {"active": False, "feedback_events": rule_events, "fallback_reason": "empty_semantic_events"}
            return {
                "active": True,
                "model_role": role,
                "provider": response.get("provider"),
                "model": response.get("model"),
                "route": response.get("route", {}),
                "feedback_events": events,
            }
        except Exception as exc:
            return {
                "active": False,
                "feedback_events": rule_events,
                "fallback_reason": f"{exc.__class__.__name__}: {exc}",
            }


def normalize_target_type(value: str) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "latest": "run",
        "task": "run",
        "trajectory": "run",
        "prop": "proposal",
        "proposal": "proposal",
        "asset": "asset",
        "pattern": "pattern",
        "run": "run",
    }
    return aliases.get(text, text or "run")


def _complex_feedback(text: str) -> bool:
    lowered = text.lower()
    return (
        len([part for part in re.split(r"[;；。]\s*|\n+|,|，|但|不过|但是|however|but", text) if part.strip()]) > 1
        or bool(_mentions_nth_proposal(lowered))
        or any(term in lowered for term in {"proposal", "skill", "memory", "rule", "asset", "pattern"})
        or any(term in text for term in {"提案", "经验", "规则", "资产", "第二条", "第三条", "不要记", "别升"})
    )


def _normalize_semantic_feedback_events(value: Any, *, raw_text: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        target_type = normalize_target_type(str(item.get("target_type") or "run"))
        if target_type not in {"run", "proposal", "asset", "pattern"}:
            target_type = "run"
        sentiment = str(item.get("sentiment") or classify_sentiment(raw_text))
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = classify_sentiment(raw_text)
        target_ref = _normalize_target_ref(str(item.get("target_ref") or item.get("target_hint") or "latest"))
        requires_confirmation = bool(item.get("requires_confirmation"))
        if target_type in {"asset", "pattern"} and sentiment == "negative":
            requires_confirmation = True
        event = {
            "target_type": target_type,
            "target_hint": target_ref,
            "sentiment": sentiment,
            "feedback_type": str(item.get("feedback_type") or infer_feedback_type(target_type, raw_text, sentiment)),
            "raw_text": str(item.get("raw_text") or raw_text),
            "strength": _clamp(item.get("strength") if item.get("strength") is not None else infer_strength(raw_text, sentiment)),
            "requires_confirmation": requires_confirmation,
            "semantic_reason": str(item.get("reason") or ""),
        }
        if sentiment == "negative" and target_type == "proposal":
            event["effect"] = {"lower_confidence": True, "recommended_action": "reject_or_edit", "suppress_similar": True}
        if sentiment == "negative" and target_type == "asset":
            event["effect"] = {"increase_negative_outcome": True, "requires_governed_rewrite_or_deprecation": True}
        result.append(event)
    return result


def _normalize_target_ref(text: str) -> str:
    lowered = text.strip().lower()
    aliases = {
        "latest": "latest",
        "current": "latest",
        "first": "nth:1",
        "1st": "nth:1",
        "second": "nth:2",
        "2nd": "nth:2",
        "third": "nth:3",
        "3rd": "nth:3",
        "fourth": "nth:4",
        "4th": "nth:4",
        "第一条": "nth:1",
        "第二条": "nth:2",
        "第三条": "nth:3",
        "第四条": "nth:4",
    }
    return aliases.get(lowered, lowered or "latest")


def _mentions_nth_proposal(text: str) -> str | None:
    match = re.search(r"(?:proposal|提案|经验)?\s*(?:#)?([1-9][0-9]*)\s*(?:st|nd|rd|th)?", text)
    if match and ("proposal" in text or "提案" in text or "经验" in text or "条" in text):
        return f"nth:{match.group(1)}"
    chinese_numbers = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    match = re.search(r"第([一二两三四五六七八九])条", text)
    if match:
        return f"nth:{chinese_numbers[match.group(1)]}"
    return None


def _clamp(value: float | int | None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))
