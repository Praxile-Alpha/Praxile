from __future__ import annotations

from statistics import mean
from typing import Any

from .config import Config
from .utils import utc_now


JUDGE_OBSERVATION_SCHEMA = "praxile.judge_observation.v1"


class JudgeGovernance:
    """Keep model self-judgment separate from objective verification and transfer evidence."""

    def __init__(self, config: Config):
        self.config = config
        self.positive_threshold = float(
            config.get("semantic_judges", "calibration", "positive_threshold", default=0.7) or 0.7
        )
        self.transfer_delta_threshold = float(
            config.get("semantic_judges", "calibration", "transfer_delta_threshold", default=0.05) or 0.05
        )

    def observe(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        self_judgment = self._self_judgment(trajectory)
        verifier_outcome = self._verifier_outcome(trajectory)
        calibration = self._calibration(self_judgment, verifier_outcome)
        return {
            "schema": JUDGE_OBSERVATION_SCHEMA,
            "task_id": trajectory.get("task_id"),
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "self_judgment": self_judgment,
            "verifier_outcome": verifier_outcome,
            "next_task_delta": {
                "status": "pending",
                "observations": [],
                "note": "Populated only when accepted experience from this run is used by a later task.",
            },
            "judgment_calibration": calibration,
            "transfer_effect": {
                "status": "pending",
                "effect": "unknown",
                "observation_count": 0,
                "mean_delta": None,
                "causal_claim": False,
            },
        }

    def transfer_observation(
        self,
        source: dict[str, Any],
        current: dict[str, Any],
        *,
        current_task_id: str,
        asset_paths: list[str],
    ) -> dict[str, Any] | None:
        source_verifier = source.get("verifier_outcome") or {}
        current_verifier = current.get("verifier_outcome") or {}
        if not source_verifier.get("available") or not current_verifier.get("available"):
            return None
        source_score = _score(source_verifier.get("score"))
        current_score = _score(current_verifier.get("score"))
        delta = round(current_score - source_score, 4)
        if delta >= self.transfer_delta_threshold:
            effect = "positive"
        elif delta <= -self.transfer_delta_threshold:
            effect = "negative"
        else:
            effect = "neutral"
        return {
            "task_id": current_task_id,
            "asset_paths": sorted(set(asset_paths)),
            "source_verifier_score": source_score,
            "next_verifier_score": current_score,
            "delta": delta,
            "effect": effect,
            "observed_at": utc_now(),
            "causal_claim": False,
            "note": "Observational transfer signal; other task and repository changes are not controlled.",
        }

    def apply_transfer(self, source: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        next_delta = source.setdefault("next_task_delta", {"status": "pending", "observations": []})
        observations = next_delta.setdefault("observations", [])
        key = (observation.get("task_id"), tuple(observation.get("asset_paths") or []))
        existing = {
            (item.get("task_id"), tuple(item.get("asset_paths") or []))
            for item in observations
            if isinstance(item, dict)
        }
        if key not in existing:
            observations.append(observation)
        next_delta["status"] = "observed" if observations else "pending"
        deltas = [float(item["delta"]) for item in observations if item.get("delta") is not None]
        effects = [str(item.get("effect") or "unknown") for item in observations]
        mean_delta = round(mean(deltas), 4) if deltas else None
        if effects and all(item == "positive" for item in effects):
            effect = "positive"
        elif "negative" in effects:
            effect = "negative"
        elif effects and all(item == "neutral" for item in effects):
            effect = "neutral"
        else:
            effect = "mixed" if effects else "unknown"
        source["transfer_effect"] = {
            "status": "observed" if effects else "pending",
            "effect": effect,
            "observation_count": len(observations),
            "mean_delta": mean_delta,
            "causal_claim": False,
        }
        source["updated_at"] = utc_now()
        return source

    @staticmethod
    def metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
        comparable = [
            item.get("judgment_calibration") or {}
            for item in observations
            if (item.get("judgment_calibration") or {}).get("comparable")
        ]
        predicted = [item for item in comparable if item.get("predicted_positive")]
        true_positive = sum(1 for item in predicted if item.get("verified_positive"))
        false_positive = sum(1 for item in predicted if not item.get("verified_positive"))
        promotion_claims = [item for item in comparable if item.get("promotion_claim")]
        false_promotions = sum(1 for item in promotion_claims if not item.get("verified_positive"))
        errors = [float(item.get("absolute_error", 0.0)) for item in comparable]
        return {
            "observation_count": len(observations),
            "comparable_count": len(comparable),
            "precision": _ratio(true_positive, true_positive + false_positive),
            "calibration_error": round(mean(errors), 4) if errors else None,
            "false_promotion_rate": _ratio(false_promotions, len(promotion_claims)),
            "promotion_claim_count": len(promotion_claims),
            "false_promotion_count": false_promotions,
        }

    def _self_judgment(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        judge = trajectory.get("llm_judge_reward") or {}
        active = bool(judge.get("active"))
        return {
            "available": active,
            "active": active,
            "score": _score(judge.get("score")) if active else None,
            "recommended_action": judge.get("recommended_action") if active else None,
            "model_role": judge.get("model_role"),
            "provider": judge.get("provider"),
            "model": judge.get("model"),
            "prompt_version": judge.get("prompt_version"),
            "reasons": list(judge.get("reasons") or []),
            "error": judge.get("error"),
            "provenance": "llm_assisted",
        }

    def _verifier_outcome(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        report = trajectory.get("reward_report") or {}
        objective = report.get("objective_signals") or {}
        tests_run = bool(objective.get("tests_run"))
        spec_status = objective.get("spec_compliance_status")
        blocked = int(objective.get("blocked_actions", 0) or 0)
        failed = int(objective.get("failed_actions", 0) or 0)
        available = bool(tests_run or spec_status is not None or blocked or failed)
        tests_passed = objective.get("tests_passed")
        passed = bool(
            available
            and tests_run
            and tests_passed is True
            and blocked == 0
            and failed == 0
            and spec_status not in {"partial", "failed"}
        )
        score = _score(report.get("objective_score_component")) if available else None
        evidence_refs: list[str] = []
        if tests_run:
            evidence_refs.append("reward_report.test_results")
        if spec_status is not None:
            evidence_refs.append("trajectory.spec_compliance")
        if blocked or failed:
            evidence_refs.append("trajectory.actions")
        return {
            "available": available,
            "passed": passed,
            "score": score,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "regression_status": objective.get("regression_status"),
            "spec_compliance_status": spec_status,
            "blocked_actions": blocked,
            "failed_actions": failed,
            "evidence_refs": evidence_refs,
            "provenance": "objective_environment",
        }

    def _calibration(self, self_judgment: dict[str, Any], verifier: dict[str, Any]) -> dict[str, Any]:
        comparable = bool(self_judgment.get("available") and verifier.get("available"))
        predicted_positive = bool(
            self_judgment.get("available")
            and _score(self_judgment.get("score")) >= self.positive_threshold
        )
        promotion_claim = bool(
            self_judgment.get("available") and self_judgment.get("recommended_action") == "accept"
        )
        verified_positive = bool(verifier.get("available") and verifier.get("passed"))
        absolute_error = None
        if comparable:
            absolute_error = round(
                abs(_score(self_judgment.get("score")) - _score(verifier.get("score"))), 4
            )
        self_only = bool(self_judgment.get("available") and not verifier.get("available"))
        return {
            "comparable": comparable,
            "positive_threshold": self.positive_threshold,
            "predicted_positive": predicted_positive,
            "verified_positive": verified_positive,
            "promotion_claim": promotion_claim,
            "correct": predicted_positive == verified_positive if comparable else None,
            "false_positive": bool(comparable and predicted_positive and not verified_positive),
            "false_promotion": bool(comparable and promotion_claim and not verified_positive),
            "absolute_error": absolute_error,
            "self_judgment_only": self_only,
            "promotion_eligible": bool(verifier.get("available") and verifier.get("passed")),
            "promotion_basis": "verifier_outcome" if verifier.get("available") else "insufficient_verifier_evidence",
        }


def _score(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return 0.0


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
