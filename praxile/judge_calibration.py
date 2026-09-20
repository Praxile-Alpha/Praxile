from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import Config
from .utils import new_id, utc_now, write_json


class JudgeCalibrationRunner:
    """Run deterministic, controlled trajectory mutations against governance detectors."""

    PROMPT_VERSION = "reward-guard-v1"

    def __init__(self, config: Config):
        self.config = config

    def run(self, suite_path: Path) -> dict[str, Any]:
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        cases = suite.get("cases") or []
        rows = [self._run_case(case) for case in cases if isinstance(case, dict)]
        expected_total = sum(len(row["expected"]) for row in rows)
        detected_total = sum(len(row["detected"]) for row in rows)
        true_positive = sum(len(set(row["expected"]) & set(row["detected"])) for row in rows)
        false_positive = sum(len(set(row["detected"]) - set(row["expected"])) for row in rows)
        false_negative = sum(len(set(row["expected"]) - set(row["detected"])) for row in rows)
        known_labels = set(str(item) for item in (suite.get("known_labels") or []))
        if not known_labels:
            known_labels = set().union(*(set(row["expected"]) | set(row["detected"]) for row in rows)) if rows else set()
        true_negative = sum(len(known_labels - (set(row["expected"]) | set(row["detected"]))) for row in rows)
        abstentions = sum(1 for row in rows if row["abstained"])
        disagreements = sum(1 for row in rows if set(row["expected"]) != set(row["detected"]))
        promotion_claims = sum(1 for row in rows if row["promotion_claim"])
        false_promotions = sum(1 for row in rows if row["false_promotion"])
        report = {
            "schema_version": 2,
            "calibration_id": new_id("judgecal"),
            "suite": str(suite_path),
            "judge": str(suite.get("judge") or "reward_guard"),
            "owner": str(suite.get("owner") or "project"),
            "model": suite.get("model"),
            "provider": suite.get("provider"),
            "model_version": suite.get("model_version"),
            "prompt_version": str(suite.get("prompt_version") or self.PROMPT_VERSION),
            "created_at": utc_now(),
            "case_count": len(rows),
            "confusion": {"true_positive": true_positive, "false_positive": false_positive, "true_negative": true_negative, "false_negative": false_negative},
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "calibration_error": _ratio(
                false_positive + false_negative,
                true_positive + false_positive + true_negative + false_negative,
            ),
            "false_promotion_rate": _ratio(false_promotions, promotion_claims),
            "promotion_claim_count": promotion_claims,
            "false_promotion_count": false_promotions,
            "abstention_rate": _ratio(abstentions, len(rows)),
            "disagreement_rate": _ratio(disagreements, len(rows)),
            "evidence_coverage": _ratio(true_positive, expected_total),
            "cases": rows,
        }
        output = self.config.paths.state / "experience" / "judge-calibration" / f"{report['calibration_id']}.json"
        write_json(output, report)
        report["path"] = str(output.relative_to(self.config.paths.root))
        return report

    def reward_policy_proposal(self, reports: list[dict[str, Any]]) -> dict[str, Any] | None:
        threshold = float(self.config.get("semantic_judges", "calibration", "min_recall", default=0.8))
        minimum = int(self.config.get("semantic_judges", "calibration", "repeated_runs", default=2))
        failing = [item for item in reports if float(item.get("recall", 1.0)) < threshold]
        if len(failing) < minimum:
            return None
        judge = str(failing[-1].get("judge") or "reward_guard")
        proposal_id = new_id("proposal")
        target = f"rules/architecture-gates/reward-policy-{judge}.md"
        content = (
            f"# Reward Policy Review: {judge}\n\n"
            "## Claim\nRepeated controlled calibration runs show reward-judge miscalibration.\n\n"
            f"## Evidence\n- Failing runs: {len(failing)}\n- Required recall: {threshold:.2f}\n"
            f"- Observed recall: {', '.join(str(item.get('recall')) for item in failing[-5:])}\n\n"
            "## Proposed Change\nReview detector/prompt/profile policy. This proposal does not self-apply.\n\n"
            "## Validation\nRe-run the same suite and held-out calibration cases before promotion.\n\n"
            "## Rollback\nRestore the previous versioned reward profile and judge prompt.\n"
        )
        return {
            "proposal_id": proposal_id,
            "source_task_id": None,
            "type": "reward_policy",
            "title": f"Review repeated {judge} miscalibration",
            "status": "pending",
            "risk_level": "high",
            "confidence": 0.85,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "target_files": [target],
            "changes": [{"path": target, "operation": "write", "content": content}],
            "architecture_gate_required": True,
            "requires_human_approval": True,
            "source": {"calibration_ids": [item.get("calibration_id") for item in failing]},
        }

    def _run_case(self, case: dict[str, Any]) -> dict[str, Any]:
        trajectory = copy.deepcopy(case.get("trajectory") or {})
        for mutation in case.get("mutations") or []:
            _apply_mutation(trajectory, mutation)
        detected = sorted(_detect(trajectory))
        expected = sorted(str(item) for item in (case.get("expected_detections") or []))
        promotion_claim = bool(case.get("promotion_claim", not detected))
        return {
            "case_id": str(case.get("case_id") or f"case-{len(expected)}"),
            "expected": expected,
            "detected": detected,
            "abstained": not detected and bool(expected),
            "promotion_claim": promotion_claim,
            "false_promotion": bool(promotion_claim and expected),
            "evidence_coverage": _ratio(len(set(expected) & set(detected)), len(expected)),
        }


def _apply_mutation(target: dict[str, Any], mutation: dict[str, Any]) -> None:
    path = [part for part in str(mutation.get("path") or "").split(".") if part]
    if not path:
        raise ValueError("Calibration mutation requires a dotted path")
    cursor: dict[str, Any] = target
    for part in path[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    if mutation.get("op", "set") == "delete":
        cursor.pop(path[-1], None)
    elif mutation.get("op", "set") == "set":
        cursor[path[-1]] = copy.deepcopy(mutation.get("value"))
    else:
        raise ValueError(f"Unsupported calibration mutation: {mutation.get('op')}")


def _detect(trajectory: dict[str, Any]) -> set[str]:
    detected: set[str] = set()
    actions = trajectory.get("actions") or []
    tests = (trajectory.get("reward_report") or {}).get("test_results") or trajectory.get("test_results") or []
    analysis = trajectory.get("task_analysis") or {}
    if (trajectory.get("result") or {}).get("status") == "failed":
        detected.add("failed_result")
    if any(item.get("status") == "failure" for item in tests if isinstance(item, dict)):
        detected.add("failed_test")
    if any(item.get("status") == "blocked" for item in actions if isinstance(item, dict)):
        detected.add("blocked_action")
    if not tests and any(item.get("action_type") == "edit_file" for item in actions if isinstance(item, dict)):
        detected.add("missing_verification")
    if analysis.get("architecture_gate_required"):
        detected.add("architecture_risk")
    if analysis.get("ui_human_review_required"):
        detected.add("ux_risk")
    if (trajectory.get("reward_report") or {}).get("evidence_graph", {}).get("unsupported_material_claims"):
        detected.add("missing_evidence")
    return detected


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
