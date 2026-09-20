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
            rollback_target={
                "component_key": candidate.component_key,
                "executor_profile": candidate.executor_profile,
                "task_family": candidate.task_family,
                "promotion_key": candidate.promotion_key,
                "version": candidate.base_version,
            },
        )

    def evaluate_lab(
        self,
        candidate: HarnessCandidate,
        lab_report: Mapping[str, Any],
        *,
        reviewer: str,
        human_approved: bool,
        thresholds: PromotionThresholds | None = None,
    ) -> CandidateEvaluation:
        report = require_mapping(lab_report, "Harness Lab report")
        if report.get("schema_version") != "praxile.executable_harness_report.v1":
            raise ControlPlaneSchemaError("unsupported Harness Lab report schema")
        if report.get("promotion_key") != candidate.promotion_key:
            raise ControlPlaneSchemaError("Harness Lab promotion key differs from the candidate")
        splits = require_mapping(report.get("splits"), "Harness Lab splits")
        heldout = require_mapping(splits.get("heldout"), "held-out Harness Lab statistics")
        delta = require_mapping(heldout.get("delta"), "held-out Harness Lab delta")
        efficiency = require_mapping(heldout.get("efficiency"), "held-out efficiency")
        invariants = require_mapping(report.get("invariant_check"), "Harness Lab invariant check")
        limits = thresholds or PromotionThresholds()
        estimate = float(delta.get("estimate", 0.0) or 0.0)
        interval = delta.get("ci95")
        if not isinstance(interval, list) or len(interval) != 2:
            raise ControlPlaneSchemaError("held-out delta requires a 95% confidence interval")
        lower = float(interval[0])
        regressions = int(heldout.get("regressions", 0) or 0)
        cost_delta = efficiency.get("cost_delta_mean")
        dead = list(report.get("dead_mechanisms") or [])
        repetitions = int(report.get("repetitions", 0) or 0)
        lab_ref = EvidenceRef("eval", str(report.get("lab_id") or "harness-lab"))
        evidence = tuple(candidate.source_evidence) + (lab_ref,)
        quality_passed = bool(report.get("promotion_eligible")) and estimate > 0 and lower >= 0 and not dead
        regression_passed = bool(invariants.get("valid")) and regressions <= limits.max_regressions and repetitions >= 2
        cost_passed = cost_delta is not None and float(cost_delta) <= limits.max_cost_increase
        approval_ref = "approval_" + hashlib.sha256(reviewer.encode("utf-8")).hexdigest()[:16]
        gates = (
            GateResult("evidence", bool(candidate.source_evidence), evidence, "Candidate and executable Harness Lab evidence are linked."),
            GateResult("quality", quality_passed, (lab_ref,), f"Held-out delta={estimate}, ci95={interval}, dead_mechanisms={dead}."),
            GateResult("regression", regression_passed, (lab_ref,), f"Regressions={regressions}, repetitions={repetitions}, invariants_valid={bool(invariants.get('valid'))}."),
            GateResult("cost", cost_passed, (lab_ref,), f"Mean held-out cost delta={cost_delta!r}, allowed increase={limits.max_cost_increase}."),
            GateResult("human", human_approved, (EvidenceRef("user_feedback", approval_ref, "Explicit promotion review"),), "Human approval recorded." if human_approved else "Human approval is still required."),
            GateResult("rollback", bool(candidate.base_version), (lab_ref,), f"Rollback target is {candidate.promotion_key}@{candidate.base_version}."),
        )
        all_passed = all(item.passed for item in gates)
        return CandidateEvaluation(
            candidate_id=candidate.candidate_id,
            baseline_ref=f"harness-lab:{report.get('lab_id')}:baseline",
            candidate_eval_ref=f"harness-lab:{report.get('lab_id')}:candidate",
            gates=gates,
            decision="promote" if all_passed else "abstain",
            reviewer=reviewer,
            metrics={
                "heldout_delta": estimate,
                "heldout_delta_ci95": interval,
                "regressions": regressions,
                "cost_delta": cost_delta,
                "dead_mechanisms": dead,
                "repetitions": repetitions,
            },
            rollback_target={
                "component_key": candidate.component_key,
                "executor_profile": candidate.executor_profile,
                "task_family": candidate.task_family,
                "promotion_key": candidate.promotion_key,
                "version": candidate.base_version,
            },
        )
