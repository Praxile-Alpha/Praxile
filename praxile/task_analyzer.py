from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .json_utils import RobustJSONError, parse_json_object


UI_TERMS = {
    "ui",
    "ux",
    "button",
    "page",
    "layout",
    "color",
    "style",
    "selected",
    "hover",
    "focus",
    "按钮",
    "页面",
    "交互",
    "导航",
    "选中态",
    "颜色",
    "样式",
    "反馈",
}

ARCHITECTURE_TERMS = {
    "architecture",
    "schema",
    "contract",
    "session",
    "permission",
    "authorization",
    "routing",
    "storage",
    "database",
    "migration",
    "shared state",
    "data flow",
    "架构",
    "数据结构",
    "契约",
    "登录态",
    "会话",
    "权限",
    "路由",
    "存储",
    "迁移",
    "共享状态",
    "同步策略",
}

AUTH_UI_SOFT_TERMS = {"auth page", "login page", "登录页", "认证页面"}
PRIVACY_TERMS = {"secret", "credential", "token", "api key", "private key", ".env", "密钥", "凭证", "令牌", "隐私"}
TEST_TERMS = {"test", "pytest", "unittest", "lint", "build", "测试", "单测", "构建"}
DOC_TERMS = {"readme", "docs", "documentation", "文档", "说明"}
REFACTOR_TERMS = {"refactor", "cleanup", "rename", "重构", "清理"}
BUG_TERMS = {"bug", "fix", "failing", "error", "exception", "修复", "错误", "失败"}
TASK_TYPES = {"feature", "bugfix", "refactor", "architecture", "ui", "docs", "test"}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass
class TaskAnalysis:
    task_type: str
    risk_level: str
    architecture_gate_required: bool
    ui_human_review_required: bool
    privacy_sensitive: bool
    high_risk: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    frozen_hits: list[dict[str, Any]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "risk_level": self.risk_level,
            "architecture_gate_required": self.architecture_gate_required,
            "ui_human_review_required": self.ui_human_review_required,
            "privacy_sensitive": self.privacy_sensitive,
            "high_risk": self.high_risk,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "signals": self.signals,
            "frozen_hits": self.frozen_hits,
            "plan": self.plan,
        }


class TaskAnalyzer:
    def __init__(self, config: Config, router: Any | None = None):
        self.config = config
        self.router = router

    def analyze(self, task: str, retrieved: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        retrieved = retrieved or []
        lower = task.lower()
        keyword_hits = sorted(term for term in ARCHITECTURE_TERMS if term in lower)
        ui_hits = sorted(term for term in UI_TERMS if term in lower)
        privacy_hits = sorted(term for term in PRIVACY_TERMS if term in lower)
        frozen_hits = [
            item for item in retrieved if item.get("kind") == "rule" and "frozen-boundaries" in item.get("path", "")
        ]
        soft_auth_ui = any(term in lower for term in AUTH_UI_SOFT_TERMS) and ui_hits
        architecture_gate_required = (bool(keyword_hits) and not soft_auth_ui) or bool(frozen_hits)
        ui_human_review_required = bool(ui_hits)
        privacy_sensitive = bool(privacy_hits)

        reasons: list[str] = []
        if keyword_hits and not soft_auth_ui:
            reasons.append("Task matched architecture-sensitive terms: " + ", ".join(keyword_hits[:8]))
        if soft_auth_ui:
            reasons.append("Auth/login wording appears scoped to UI changes, not shared auth/session contracts.")
        if frozen_hits:
            reasons.append("Accepted frozen boundary matched retrieved context.")
        if ui_human_review_required:
            reasons.append("Task is UI/UX-sensitive and needs human acceptance for perception.")
        if privacy_sensitive:
            reasons.append("Task mentions secrets, credentials, tokens, or private environment data.")

        plan = self._plan(frozen_hits=bool(frozen_hits), architecture_gate_required=architecture_gate_required)
        llm_assisted = bool(self.config.get("task_analysis", "llm_assisted", default=False))
        llm_signal: dict[str, Any] = {
            "enabled": llm_assisted,
            "used": False,
            "model_role": self.config.get("task_analysis", "llm_model_role", default="planning_model"),
        }
        llm_payload: dict[str, Any] | None = None
        if llm_assisted and self.router is not None:
            llm_payload = self._llm_analyze(task, retrieved)
            llm_signal.update(llm_payload.get("_signal", {}))
        elif llm_assisted:
            llm_signal["error"] = "router_not_available"
            reasons.append("LLM-assisted task analysis is configured but no model router is attached.")

        if llm_payload:
            llm_signal["used"] = True
            if _as_bool(llm_payload.get("architecture_gate_required")):
                architecture_gate_required = True
            if _as_bool(llm_payload.get("ui_human_review_required")):
                ui_human_review_required = True
            if _as_bool(llm_payload.get("privacy_sensitive")):
                privacy_sensitive = True
            llm_reasons = [str(item) for item in llm_payload.get("reasons", []) if str(item).strip()]
            reasons.extend(f"LLM-assisted analysis: {reason}" for reason in llm_reasons[:4])

        task_type = self._task_type(lower, architecture_gate_required, ui_human_review_required)
        if llm_payload and isinstance(llm_payload.get("task_type"), str):
            proposed_type = llm_payload["task_type"].strip().lower()
            if proposed_type == "architecture":
                task_type = "architecture"
                architecture_gate_required = True
            elif proposed_type in TASK_TYPES and not architecture_gate_required:
                task_type = proposed_type
        risk_level = "high" if architecture_gate_required else "medium" if privacy_sensitive or ui_human_review_required else "low"
        if llm_payload and isinstance(llm_payload.get("risk_level"), str):
            risk_level = _max_risk(risk_level, llm_payload["risk_level"])
        high_risk = risk_level in {"medium", "high"}
        confidence = 0.82 if architecture_gate_required or ui_human_review_required or privacy_sensitive else 0.68
        if llm_payload:
            confidence = max(confidence, _confidence(llm_payload.get("confidence"), default=0.0))
        if architecture_gate_required and not any("Pause implementation" in item for item in plan):
            plan = self._plan(frozen_hits=bool(frozen_hits), architecture_gate_required=True)

        return TaskAnalysis(
            task_type=task_type,
            risk_level=risk_level,
            architecture_gate_required=architecture_gate_required,
            ui_human_review_required=ui_human_review_required,
            privacy_sensitive=privacy_sensitive,
            high_risk=high_risk,
            confidence=confidence,
            reasons=reasons or ["No high-risk deterministic task-analysis signals matched."],
            signals={
                "keyword_hits": keyword_hits,
                "ui_hits": ui_hits,
                "privacy_hits": privacy_hits,
                "frozen_boundary_hits": [item.get("path") for item in frozen_hits],
                "llm_assisted": bool(llm_signal.get("used")),
                "llm_assisted_configured": llm_assisted,
                "llm": llm_signal,
            },
            frozen_hits=frozen_hits,
            plan=plan,
        ).to_dict()

    def _llm_analyze(self, task: str, retrieved: list[dict[str, Any]]) -> dict[str, Any] | None:
        role = str(self.config.get("task_analysis", "llm_model_role", default="planning_model"))
        purpose = _purpose_from_role(role)
        timeout = int(self.config.get("task_analysis", "llm_timeout_seconds", default=12))
        max_tokens = int(self.config.get("task_analysis", "llm_max_tokens", default=800))
        context = "\n".join(
            f"- {item.get('kind')} {item.get('path')} {str(item.get('snippet', ''))[:240]}"
            for item in retrieved[:6]
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Classify a local code-project task for Praxile. Return exactly one JSON object. "
                    "Be conservative about shared contracts, auth/session, routing, storage, migrations, privacy, and UX. "
                    "Do not use markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task:\n{task}\n\n"
                    f"Retrieved project rules/memory:\n{context or '(none)'}\n\n"
                    "JSON schema:\n"
                    "{"
                    "\"task_type\":\"feature|bugfix|refactor|architecture|ui|docs|test\","
                    "\"risk_level\":\"low|medium|high\","
                    "\"architecture_gate_required\":false,"
                    "\"ui_human_review_required\":false,"
                    "\"privacy_sensitive\":false,"
                    "\"confidence\":0.0,"
                    "\"reasons\":[\"short evidence\"]"
                    "}"
                ),
            },
        ]
        try:
            response = self.router.chat(
                messages,
                purpose=purpose,
                temperature=0,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            payload = parse_json_object(response.get("content", ""))
        except RobustJSONError as exc:
            return {"_signal": {"error": f"invalid_json: {exc}", "purpose": purpose, "model_role": role}}
        except Exception as exc:
            return {"_signal": {"error": f"{exc.__class__.__name__}: {exc}", "purpose": purpose, "model_role": role}}
        payload["_signal"] = {
            "purpose": purpose,
            "model_role": role,
            "provider": response.get("provider"),
            "model": response.get("model"),
            "latency_ms": response.get("latency_ms"),
        }
        return payload

    def _task_type(self, lower: str, architecture: bool, ui: bool) -> str:
        if architecture:
            return "architecture"
        if ui:
            return "ui"
        if any(term in lower for term in DOC_TERMS):
            return "docs"
        if any(term in lower for term in TEST_TERMS):
            return "test"
        if any(term in lower for term in REFACTOR_TERMS):
            return "refactor"
        if any(term in lower for term in BUG_TERMS):
            return "bugfix"
        return "feature"

    def _plan(self, *, frozen_hits: bool, architecture_gate_required: bool) -> list[str]:
        plan = [
            "Load relevant memory, skills, evals, and frozen-boundary rules.",
            "Inspect the project map and identify likely target files.",
            "Route the task by privacy, complexity, and risk before model calls.",
            "Use guarded file/search/shell actions only through environment adapters.",
            "Keep edits scoped and record every action in the trajectory.",
            "Run configured verification and generate reward/proposals for user approval.",
        ]
        if frozen_hits:
            plan.insert(1, "Check accepted frozen boundaries before changing shared contracts.")
        if architecture_gate_required:
            plan.insert(2, "Pause implementation and create an architecture gate proposal before edits.")
        return plan


def _purpose_from_role(role: str) -> str:
    return role[:-6] if role.endswith("_model") else role


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return bool(value)


def _max_risk(current: str, proposed: str) -> str:
    proposed = proposed.strip().lower()
    if proposed not in RISK_ORDER:
        return current
    return proposed if RISK_ORDER[proposed] > RISK_ORDER.get(current, 0) else current


def _confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(parsed, 1.0))
