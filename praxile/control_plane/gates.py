from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from .common import ControlPlaneSchemaError, EvidenceRef, require_mapping
from .evolution import CandidateEvaluation, GateResult, HarnessCandidate


@dataclass(frozen=True)
class PromotionThresholds:
    max_regressions: int = 0
    max_cost_increase: float = 0.0
    require_quality_improvement: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.max_regressions, bool) or self.max_regressions < 0:
            raise ControlPlaneSchemaError("max_regressions must be non-negative")
        if self.max_cost_increase < 0:
            raise ControlPlaneSchemaError("max_cost_increase must be non-negative")


class PromotionGateEvaluator:
    """Translate an invariant-checked A/B result into all six promotion gates."""

    def evaluate(
        self,
        candidate: HarnessCandidate,
        ab_report: Mapping[str, Any],
        *,
        reviewer: str,
        human_approved: bool,
        thresholds: PromotionThresholds | None = None,
    ) -> CandidateEvaluation:
        report = require_mapping(ab_report, "A/B report")
        comparison = require_mapping(report.get("comparison"), "A/B comparison")
        invariants = require_mapping(report.get("invariant_check"), "A/B invariant check")
        limits = thresholds or PromotionThresholds()
        baseline_id = str(require_mapping(report.get("baseline"), "baseline arm").get("eval_run_id") or "")
        candidate_id = str(require_mapping(report.get("candidate"), "candidate arm").get("eval_run_id") or "")
        if not baseline_id or not candidate_id:
            raise ControlPlaneSchemaError("A/B report must identify both eval runs")
        eval_refs = (EvidenceRef("eval", baseline_id), EvidenceRef("eval", candidate_id))
        decision = str(comparison.get("decision") or "inconclusive")
        totals = require_mapping(comparison.get("totals", {}), "A/B totals")
        regressions = int(totals.get("regressions", 0))
        cost_delta = totals.get("cost_delta")
        task_results = comparison.get("task_results", [])
        if not isinstance(task_results, list):
            raise ControlPlaneSchemaError("A/B task_results must be an array")
        has_unknown = any(
            isinstance(item, Mapping) and item.get("transition") == "unknown" for item in task_results
        )
        scope_results = [
            item.get("diff_scope")
            for item in task_results
            if isinstance(item, Mapping)
        ]
        diff_scope_known = bool(task_results) and all(
            isinstance(item, Mapping) and item.get("candidate_status") != "missing"
            for item in scope_results
        )
        diff_scope_passed = diff_scope_known and all(
            isinstance(item, Mapping) and item.get("passed") is True
            for item in scope_results
        )
        outcome_quality_passed = decision == "improve" or (
            not limits.require_quality_improvement and decision == "inconclusive" and not has_unknown
        )
        quality_passed = outcome_quality_passed and diff_scope_passed
        regression_passed = bool(invariants.get("valid")) and regressions <= limits.max_regressions
        cost_passed = cost_delta is not None and float(cost_delta) <= limits.max_cost_increase
        approval_ref = "approval_" + hashlib.sha256(reviewer.encode("utf-8")).hexdigest()[:16]
        gates = (
            GateResult("evidence", bool(candidate.source_evidence), candidate.source_evidence, "Candidate has typed source evidence."),
            GateResult(
                "quality",
                quality_passed,
                eval_refs,
                f"A/B decision is {decision}; diff_scope_known={diff_scope_known}, diff_scope_passed={diff_scope_passed}.",
            ),
            GateResult("regression", regression_passed, eval_refs, f"Regressions={regressions}, budget={limits.max_regressions}, invariants_valid={bool(invariants.get('valid'))}."),
            GateResult("cost", cost_passed, eval_refs, f"Cost delta={cost_delta!r}, allowed increase={limits.max_cost_increase}."),
            GateResult("human", human_approved, (EvidenceRef("user_feedback", approval_ref, "Explicit promotion review"),), "Human approval recorded." if human_approved else "Human approval is still required."),
            GateResult("rollback", bool(candidate.base_version), eval_refs, f"Rollback target is {candidate.component_key}@{candidate.base_version}."),
        )
        all_passed = all(item.passed for item in gates)
        final_decision = "promote" if all_passed else "abstain" if decision == "inconclusive" or not human_approved else "reject"
        return CandidateEvaluation(
            candidate_id=candidate.candidate_id,
            baseline_ref=f"eval:{baseline_id}",
            candidate_eval_ref=f"eval:{candidate_id}",
            gates=gates,
            decision=final_decision,
            reviewer=reviewer,
            metrics={
                "comparison_decision": decision,
                "regressions": regressions,
                "cost_delta": cost_delta,
                "unknown_task_result": has_unknown,
                "diff_scope_known": diff_scope_known,
                "diff_scope_passed": diff_scope_passed,
            },
            rollback_target={"component_key": candidate.component_key, "version": candidate.base_version},
        )
