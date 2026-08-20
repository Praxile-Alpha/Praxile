from __future__ import annotations

import json
from typing import Any

from .config import Config
from .utils import stable_hash, utc_now


PROVENANCE_VALUES = {"objective", "derived", "llm_assisted", "human_confirmed"}


class RewardProfileRegistry:
    """Resolve and freeze the exact reward policy used by one run."""

    def __init__(self, config: Config | None):
        self.config = config

    def resolve(self, trajectory: dict[str, Any], effective: dict[str, Any]) -> dict[str, Any]:
        analysis = trajectory.get("task_analysis") if isinstance(trajectory.get("task_analysis"), dict) else {}
        task_class = str(analysis.get("task_class") or analysis.get("task_type") or "general")
        repository = self.config.paths.root.name if self.config else "unconfigured"
        configured = self.config.get("reward", "profiles", default={}) if self.config else {}
        configured = configured if isinstance(configured, dict) else {}
        selected = str((configured.get("task_classes") or {}).get(task_class) or configured.get("default") or "default")
        definition = (configured.get("definitions") or {}).get(selected) or {}
        snapshot = {
            "profile_id": f"{repository}:{selected}:{task_class}",
            "repository": repository,
            "task_class": task_class,
            "name": selected,
            "declared_version": str(definition.get("version") or "auto"),
            "effective_policy": effective,
        }
        snapshot["profile_version"] = stable_hash(json.dumps(snapshot, sort_keys=True, ensure_ascii=False), length=20)
        snapshot["resolved_at"] = utc_now()
        return snapshot

    def overrides(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        analysis = trajectory.get("task_analysis") if isinstance(trajectory.get("task_analysis"), dict) else {}
        task_class = str(analysis.get("task_class") or analysis.get("task_type") or "general")
        configured = self.config.get("reward", "profiles", default={}) if self.config else {}
        configured = configured if isinstance(configured, dict) else {}
        selected = str((configured.get("task_classes") or {}).get(task_class) or configured.get("default") or "default")
        definition = (configured.get("definitions") or {}).get(selected) or {}
        overrides = definition.get("overrides") or {}
        return overrides if isinstance(overrides, dict) else {}


class RewardEvidenceBuilder:
    MATERIAL_CLAIMS = (
        "task_success",
        "process_safety",
        "regression_stability",
        "cost_control",
        "experience_value",
        "final_reward",
    )

    def build(
        self,
        trajectory: dict[str, Any],
        test_results: list[dict[str, Any]],
        report: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(trajectory.get("task_id") or "unpersisted")
        nodes: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []

        def evidence(kind: str, source_ref: str, payload: Any, provenance: str) -> str:
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
            evidence_id = "evidence:" + stable_hash(f"{task_id}|{kind}|{source_ref}|{canonical}", length=24)
            nodes.append(
                {
                    "evidence_id": evidence_id,
                    "type": kind,
                    "source_ref": source_ref,
                    "provenance": provenance,
                    "payload": payload,
                }
            )
            return evidence_id

        test_refs = [evidence("test", f"test:{index}", item, "objective") for index, item in enumerate(test_results)]
        action_refs: list[str] = []
        for index, action in enumerate(trajectory.get("actions") or []):
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("action_type") or "action")
            if action_type in {"run_command", "run_test", "architecture_gate"} or action.get("status") in {"blocked", "failure"}:
                action_refs.append(
                    evidence(
                        "command" if action_type in {"run_command", "run_test"} else "safety_action",
                        f"action:{index}",
                        {
                            "action_type": action_type,
                            "status": action.get("status"),
                            "input": action.get("input"),
                            "risk_level": action.get("risk_level") or (action.get("observation") or {}).get("risk_level"),
                        },
                        "objective",
                    )
                )
        diff = trajectory.get("diff_summary") or {}
        diff_refs = [evidence("diff", "trajectory.diff_summary", diff, "objective")] if diff else []
        spec = trajectory.get("spec_compliance") or {}
        spec_refs = [evidence("spec", "trajectory.spec_compliance", spec, "objective")] if spec else []
        screenshots = list(trajectory.get("screenshots") or [])
        for action in trajectory.get("actions") or []:
            if not isinstance(action, dict):
                continue
            observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
            if data.get("artifact_type") == "screenshot" or action.get("action_type") == "browser_screenshot":
                screenshots.append(data or observation)
        screenshot_refs = [
            evidence("screenshot", f"screenshot:{index}", item, "objective")
            for index, item in enumerate(screenshots)
        ]
        feedback = report.get("user_feedback_reward") or {}
        feedback_refs = [evidence("user_decision", "trajectory.user_feedback_reward", feedback, "human_confirmed")] if feedback.get("active") else []
        judge = report.get("llm_judge_reward") or {}
        judge_refs = [evidence("judge_output", "trajectory.llm_judge_reward", judge, "llm_assisted")] if judge.get("active") else []
        cost_refs = [evidence("cost", "trajectory.cost", trajectory.get("cost") or {}, "objective")]
        result_refs = [evidence("result", "trajectory.result", trajectory.get("result") or {}, "objective")]

        def claim(name: str, value: Any, provenance: str, refs: list[str]) -> None:
            claim_id = "claim:" + stable_hash(f"{task_id}|{name}|{profile['profile_version']}", length=24)
            claims.append(
                {
                    "claim_id": claim_id,
                    "claim_type": name,
                    "value": value,
                    "provenance": provenance,
                    "evidence_refs": list(dict.fromkeys(refs)),
                    "status": "supported" if refs else "unsupported",
                    "profile_version": profile["profile_version"],
                }
            )

        claim("task_success", report.get("task_success"), "derived", result_refs + diff_refs + spec_refs + screenshot_refs)
        claim("process_safety", report.get("process_safety"), "derived", action_refs or result_refs)
        claim("regression_stability", report.get("regression_score"), "derived", test_refs + spec_refs)
        claim("cost_control", report.get("cost_score"), "derived", cost_refs)
        claim("experience_value", report.get("experience_value"), "derived", diff_refs + test_refs + action_refs + spec_refs)
        final_refs = [item["evidence_id"] for item in nodes if item["type"] in {"result", "test", "cost", "safety_action", "diff", "spec", "screenshot"}]
        final_refs.extend(feedback_refs + judge_refs)
        claim("final_reward", report.get("overall"), "derived", final_refs)

        supported = sum(1 for item in claims if item["status"] == "supported")
        coverage = round(supported / len(claims), 3) if claims else 0.0
        unsupported = [item["claim_type"] for item in claims if item["status"] != "supported"]
        escalation_reasons: list[str] = []
        analysis = trajectory.get("task_analysis") or {}
        if unsupported:
            escalation_reasons.append("material_reward_claim_missing_evidence")
        if analysis.get("architecture_gate_required"):
            escalation_reasons.append("architecture_risk")
        if analysis.get("ui_human_review_required"):
            escalation_reasons.append("ux_risk")
        if _judge_disagrees(report):
            escalation_reasons.append("judge_disagreement")
        return {
            "schema_version": 1,
            "nodes": nodes,
            "claims": claims,
            "edges": [
                {"source": ref, "target": item["claim_id"], "relation": "supports_reward_claim"}
                for item in claims
                for ref in item["evidence_refs"]
            ],
            "coverage": coverage,
            "unsupported_material_claims": unsupported,
            "escalation": {
                "required": bool(escalation_reasons),
                "reasons": list(dict.fromkeys(escalation_reasons)),
                "blocks_automatic_policy_update": True,
            },
        }


def _judge_disagrees(report: dict[str, Any]) -> bool:
    judge = report.get("llm_judge_reward") or {}
    if not judge.get("active"):
        return False
    recommendation = str(judge.get("recommended_action") or "inspect")
    objective = float(report.get("objective_score_component", 0.0) or 0.0)
    judge_score = float(judge.get("score", 0.0) or 0.0)
    return abs(objective - judge_score) >= 0.35 or (recommendation == "accept" and objective < 0.5) or (recommendation == "reject_or_edit" and objective >= 0.8)
