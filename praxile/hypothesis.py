from __future__ import annotations

from typing import Any
from .utils import new_id


class HypothesisGenerator:
    """
    Generates experience hypotheses from mined patterns.
    """

    @staticmethod
    def generate(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hypotheses: list[dict[str, Any]] = []
        for pat in patterns:
            evidence_count = len(pat.get("episodes", []))
            confidence = pat.get("confidence", 0.5)
            
            # Determine asset type
            asset_type = "memory_update"
            if evidence_count >= 2:
                asset_type = "failure_pattern"
            if evidence_count >= 3 and confidence > 0.8:
                asset_type = "project_pattern"
                
            hypotheses.append({
                "hypothesis_id": new_id("hyp"),
                "pattern_id": pat.get("pattern_id"),
                "category": pat.get("category"),
                "claim": pat.get("candidate_hypothesis", "Unknown claim"),
                "evidence": pat.get("episodes", []),
                "evidence_items": pat.get("evidence_items", []),
                "evidence_count": evidence_count,
                "source_episodes": pat.get("source_episodes", []),
                "applies_when": pat.get("applies_when", []),
                "does_not_apply_when": pat.get("does_not_apply_when", []),
                "failure_signatures": pat.get("failure_signatures", pat.get("signature_terms", [])),
                "fix_strategy": pat.get("fix_strategy", []),
                "verification_commands": pat.get("verification_commands", []),
                "affected_files": pat.get("affected_files", []),
                "counterexamples": pat.get("counterexamples", []),
                "expected_future_use": pat.get("expected_future_use"),
                "success_count": pat.get("success_count"),
                "failure_count": pat.get("failure_count", 0),
                "pattern_score": pat.get("pattern_score"),
                "match_dimensions": pat.get("match_dimensions", {}),
                "match_reasons": pat.get("match_reasons", []),
                "semantic_reasons": pat.get("semantic_reasons", []),
                "recommended_pattern_claim": pat.get("recommended_pattern_claim"),
                "positive_feedback_count": pat.get("positive_feedback_count", 0),
                "negative_feedback_count": pat.get("negative_feedback_count", 0),
                "latest_feedback": pat.get("latest_feedback", []),
                "confidence_adjustment_from_feedback": pat.get("confidence_adjustment_from_feedback", 0.0),
                "confidence": confidence,
                "confidence_rationale": pat.get("confidence_rationale") or (
                    f"{evidence_count} source episode(s), "
                    f"{pat.get('success_count', evidence_count)} successful observation(s), "
                    f"{pat.get('failure_count', 0)} counterexample(s)."
                ),
                "suggested_asset_type": asset_type
            })
        return hypotheses


class CounterexampleChecker:
    """
    Validates hypotheses against counterexamples.
    """

    @staticmethod
    def validate(
        hypotheses: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        semantic_checker: Any | None = None,
    ) -> list[dict[str, Any]]:
        context = context or {}
        validated = []
        for hyp in hypotheses:
            pattern_episode_ids = {str(item) for item in hyp.get("evidence", []) if item}
            signatures = {str(item) for item in hyp.get("failure_signatures", []) if item}
            category = str(hyp.get("category") or "")
            known_fix_terms = _terms(" ".join(str(item) for item in hyp.get("fix_strategy", [])))
            affected_files = {str(item) for item in hyp.get("affected_files", []) if item}
            counterexamples = list(hyp.get("counterexamples", []) or [])
            seen = {str(item.get("counterexample_id") or item.get("episode_id") or item.get("task_id") or item) for item in counterexamples if isinstance(item, dict)}
            for ep in episodes:
                episode_id = str(ep.get("episode_id") or "")
                if episode_id and episode_id in pattern_episode_ids:
                    continue
                same_category = bool(category and category == str(ep.get("category") or ""))
                raw_signatures = ep.get("failure_signatures") or []
                if not isinstance(raw_signatures, list):
                    raw_signatures = [raw_signatures]
                episode_signatures = {str(item) for item in [ep.get("failure_signature"), *raw_signatures] if item}
                same_signature = bool(signatures and signatures.intersection(episode_signatures))
                same_files = bool(affected_files and affected_files.intersection({str(item) for item in _episode_files(ep)}))
                if not (same_category and (same_signature or same_files)):
                    continue
                reason = None
                counterexample_type = None
                delta = -0.1
                outcome = str(ep.get("outcome") or ep.get("status") or "").lower()
                if outcome in {"failed", "failure", "needs_human", "rejected"}:
                    reason = f"Same signature/category ended with `{outcome}`."
                    counterexample_type = "negative_outcome"
                    delta = -0.18
                episode_fix_terms = _terms(
                    " ".join(str(item) for item in (ep.get("fix_strategy") or []))
                    + " "
                    + str(ep.get("fix_pattern") or "")
                )
                if not reason and known_fix_terms and episode_fix_terms and not known_fix_terms.intersection(episode_fix_terms):
                    reason = "Same signature/category used a materially different fix strategy."
                    counterexample_type = "same_signature_different_fix" if same_signature else "same_file_different_fix"
                    delta = -0.22
                if not reason and same_files:
                    known_root = _terms(str(hyp.get("claim") or "") + " " + " ".join(str(item) for item in hyp.get("evidence_items", [])))
                    episode_root = _terms(str(ep.get("root_cause") or "") + " " + str(ep.get("symptom") or ""))
                    if known_root and episode_root and not known_root.intersection(episode_root):
                        reason = "Same affected file showed a different symptom or root cause."
                        counterexample_type = "same_file_different_root_cause"
                        delta = -0.14
                semantic_result = (
                    semantic_checker.check(hyp, ep, heuristic_reason=reason)
                    if semantic_checker is not None and (same_signature or same_files or reason)
                    else None
                )
                if semantic_result and semantic_result.get("active"):
                    if not semantic_result.get("is_counterexample"):
                        continue
                    reason = str(semantic_result.get("reason") or reason or "Semantic checker identified a counterexample.")
                    counterexample_type = str(semantic_result.get("counterexample_type") or counterexample_type or "semantic_counterexample")
                    delta = float(semantic_result.get("confidence_delta") or delta)
                if reason:
                    key = f"{episode_id}-{counterexample_type}" or str(ep.get("task_id") or reason)
                    if key in seen:
                        continue
                    seen.add(key)
                    ce_id = f"ce_{_small_hash(str(hyp.get('hypothesis_id') or '') + key)}"
                    counterexamples.append(
                        {
                            "counterexample_id": ce_id,
                            "target_hypothesis": hyp.get("hypothesis_id"),
                            "episode_id": ep.get("episode_id"),
                            "task_id": ep.get("task_id"),
                            "type": counterexample_type or "episode_counterexample",
                            "reason": reason,
                            "effect": "lower_confidence",
                            "confidence_delta": delta,
                            "recommended_action": semantic_result.get("recommended_action", "inspect_or_edit")
                            if isinstance(semantic_result, dict)
                            else "inspect_or_edit",
                            "failure_signature": ep.get("failure_signature"),
                            "fix_pattern": ep.get("fix_pattern"),
                            "semantic_checker": {
                                key: semantic_result.get(key)
                                for key in ["active", "role", "provider", "model", "route"]
                            }
                            if isinstance(semantic_result, dict) and semantic_result.get("active")
                            else None,
                        }
                    )
            for item in _context_counterexamples(hyp, context):
                key = str(item.get("counterexample_id") or item)
                if key not in seen:
                    seen.add(key)
                    counterexamples.append(item)
            
            if counterexamples:
                hyp["counterexamples"] = counterexamples
                delta = sum(float(item.get("confidence_delta") or -0.1) for item in counterexamples if isinstance(item, dict))
                hyp["confidence"] = max(0.1, round(float(hyp.get("confidence", 0.5)) + max(delta, -0.45), 3))
                hyp["recommended_action"] = "inspect_or_edit"
                hyp["counterexample_note"] = "; ".join(str(item.get("reason") or item.get("type")) for item in counterexamples[:3] if isinstance(item, dict))
            else:
                hyp["counterexamples"] = []
                hyp["recommended_action"] = "accept" if hyp["confidence"] > 0.7 else "inspect"
            
            validated.append(hyp)
        return validated


def _terms(text: str) -> set[str]:
    return {part.lower() for part in str(text or "").replace("`", " ").replace("/", " ").replace("-", " ").split() if len(part) > 2}


def _episode_files(ep: dict[str, Any]) -> list[str]:
    values = []
    for key in ["affected_files", "touched_files"]:
        raw = ep.get(key) or []
        values.extend(raw if isinstance(raw, list) else [raw])
    scope = ep.get("scope", {}) if isinstance(ep.get("scope"), dict) else {}
    raw_scope = scope.get("applies_to") or []
    values.extend(raw_scope if isinstance(raw_scope, list) else [raw_scope])
    return [str(item) for item in values if item]


def _context_counterexamples(hyp: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    claim_terms = _terms(str(hyp.get("claim") or "") + " " + " ".join(str(item) for item in hyp.get("evidence_items", [])))
    for proposal in context.get("rejected_proposals", []) or []:
        proposal_terms = _terms(
            " ".join(
                [
                    str(proposal.get("type") or ""),
                    str(proposal.get("title") or ""),
                    str(proposal.get("reason") or ""),
                    str(proposal.get("evidence_summary") or ""),
                    " ".join(str(item) for item in proposal.get("evidence") or []),
                ]
            )
        )
        if claim_terms and proposal_terms and len(claim_terms & proposal_terms) / max(1, len(claim_terms | proposal_terms)) >= 0.18:
            result.append(
                {
                    "counterexample_id": f"ce_{_small_hash(str(hyp.get('hypothesis_id')) + str(proposal.get('proposal_id')))}",
                    "target_hypothesis": hyp.get("hypothesis_id"),
                    "source_task_id": proposal.get("source_task_id"),
                    "type": "rejected_proposal_similarity",
                    "reason": f"Similar proposal `{proposal.get('proposal_id')}` was previously rejected.",
                    "effect": "lower_confidence",
                    "confidence_delta": -0.16,
                    "recommended_action": "inspect_or_edit",
                }
            )
    for feedback in context.get("feedback", []) or []:
        if feedback.get("sentiment") != "negative":
            continue
        feedback_terms = _terms(str(feedback.get("raw_text") or "") + " " + str(feedback.get("target_id") or ""))
        if claim_terms and feedback_terms and claim_terms.intersection(feedback_terms):
            result.append(
                {
                    "counterexample_id": f"ce_{_small_hash(str(hyp.get('hypothesis_id')) + str(feedback.get('feedback_id')))}",
                    "target_hypothesis": hyp.get("hypothesis_id"),
                    "source_task_id": feedback.get("target_id"),
                    "type": "negative_user_feedback",
                    "reason": "Negative user feedback overlaps with this pattern claim or evidence.",
                    "effect": "lower_confidence",
                    "confidence_delta": -0.12,
                    "recommended_action": "inspect_or_edit",
                }
            )
    return result


def _small_hash(text: str) -> str:
    value = 0
    for char in text:
        value = (value * 33 + ord(char)) % 0xFFFFFFFF
    return f"{value:08x}"[:8]
