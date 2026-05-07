from __future__ import annotations

from typing import Any
from .utils import new_id, slugify, utc_now


class ProposalComposer:
    """
    Composes high-quality project-specific proposals from validated hypotheses.
    """

    @staticmethod
    def compose(hypotheses: list[dict[str, Any]], engine: Any) -> list[dict[str, Any]]:
        proposals = []
        for hyp in hypotheses:
            asset_type = hyp.get("suggested_asset_type")
            if asset_type == "project_pattern":
                proposals.append(ProposalComposer._project_pattern(hyp, engine))
            elif asset_type == "failure_pattern":
                proposals.append(ProposalComposer._failure_pattern(hyp, engine))
            elif asset_type == "skill_refinement":
                proposals.append(ProposalComposer._skill_refinement(hyp, engine))
        
        # Filter out Nones
        return [p for p in proposals if p is not None]

    @staticmethod
    def _project_pattern(hyp: dict[str, Any], engine: Any) -> dict[str, Any] | None:
        title = slugify(hyp.get("claim", "project-pattern")[:40], max_length=48)
        claim = str(hyp.get("claim") or "Unknown project pattern")
        applies_when = _as_lines(
            hyp.get("applies_when"),
            fallback=["Future tasks match the same failure signature, files, commands, and source episodes."],
        )
        does_not_apply_when = _as_lines(
            hyp.get("does_not_apply_when"),
            fallback=["Future tasks have unrelated files, commands, architecture, security, or data-flow constraints."],
        )
        evidence_items = _as_lines(
            hyp.get("evidence_items") or hyp.get("evidence"),
            fallback=[f"{hyp.get('evidence_count', 0)} source episode(s) were grouped by the pattern miner."],
        )
        failure_signatures = _as_lines(hyp.get("failure_signatures"), fallback=["No explicit failure signature recorded."])
        fix_strategy = _as_lines(
            hyp.get("fix_strategy"),
            fallback=["Use the source episodes as evidence for a scoped repair, then verify before accepting."],
        )
        verification_commands = _as_lines(
            hyp.get("verification_commands"),
            fallback=["No reusable verification command recorded; require human/project-specific verification."],
            code=True,
        )
        counterexamples = _counterexample_lines(hyp.get("counterexamples"))
        semantic_reasons = _as_lines(hyp.get("semantic_reasons"), fallback=[])
        source_episodes = _source_episode_lines(hyp.get("source_episodes") or hyp.get("evidence"))
        confidence = hyp.get("confidence", 0.8)
        confidence_rationale = str(hyp.get("confidence_rationale") or f"Evidence count: {hyp.get('evidence_count', 0)}.")
        expected_future_use = str(
            hyp.get("expected_future_use")
            or "Load this card before similar future tasks and use it as a candidate strategy, not an automatic rule."
        )
        content = (
            f"# Project Pattern: {title}\n\n"
            f"## Claim\n{claim}\n\n"
            "## Applies When\n" + _bullet_block(applies_when) + "\n\n"
            "## Does Not Apply When\n" + _bullet_block(does_not_apply_when) + "\n\n"
            "## Evidence\n" + _bullet_block(evidence_items) + "\n\n"
            "## Failure Signatures\n" + _bullet_block(failure_signatures) + "\n\n"
            "## Fix Strategy\n" + _bullet_block(fix_strategy) + "\n\n"
            "## Verification Commands\n" + _bullet_block(verification_commands) + "\n\n"
            "## Counterexamples\n" + _bullet_block(counterexamples) + "\n\n"
            + ("## Semantic Judge Notes\n" + _bullet_block(semantic_reasons) + "\n\n" if semantic_reasons else "")
            + "## Source Episodes\n" + _bullet_block(source_episodes) + "\n\n"
            f"## Confidence\n{confidence}\n\n{confidence_rationale}\n\n"
            f"## Expected Future Use\n{expected_future_use}\n"
        )
        proposal = engine._proposal(
            source_task_id="mine_patterns",
            proposal_type="project_pattern",
            title=f"Record project pattern `{title}`",
            reason="Mined from multiple cross-run episodes.",
            risk_level="medium",
            evidence=[
                f"Evidence count: {hyp.get('evidence_count')}",
                *evidence_items[:4],
                *source_episodes[:3],
            ],
            confidence=confidence,
            affected_files=hyp.get("affected_files", []),
            trigger_reason="Pattern miner found repeating successful outcomes.",
            future_applicability=expected_future_use,
            applicability_scope="\n".join(f"- {item}" for item in applies_when),
            anti_scope="\n".join(f"- {item}" for item in does_not_apply_when),
            changes=[{"path": f"experience/patterns/{title}.md", "operation": "write", "content": content}]
        )
        proposal["pattern_score"] = hyp.get("pattern_score")
        proposal["match_dimensions"] = hyp.get("match_dimensions", {})
        proposal["match_reasons"] = hyp.get("match_reasons", [])
        proposal["semantic_reasons"] = semantic_reasons
        if hyp.get("recommended_pattern_claim"):
            proposal["recommended_pattern_claim"] = hyp.get("recommended_pattern_claim")
        proposal["confidence_rationale"] = confidence_rationale
        proposal["counterexamples"] = hyp.get("counterexamples", [])
        proposal["recommended_action_override"] = hyp.get("recommended_action") if hyp.get("recommended_action") in {"inspect", "inspect_or_edit", "reject_or_edit"} else None
        if proposal["recommended_action_override"] == "inspect_or_edit":
            proposal["recommended_action_override"] = "inspect"
        if hyp.get("latest_feedback"):
            proposal["feedback_influence"] = [
                {
                    "type": "pattern_feedback",
                    "positive": hyp.get("positive_feedback_count", 0),
                    "negative": hyp.get("negative_feedback_count", 0),
                    "confidence_delta": hyp.get("confidence_adjustment_from_feedback", 0),
                }
            ]
        return proposal

    @staticmethod
    def _failure_pattern(hyp: dict[str, Any], engine: Any) -> dict[str, Any] | None:
        title = slugify(hyp.get("claim", "failure-pattern")[:40], max_length=48)
        content = (
            f"# Failure Pattern: {title}\n\n"
            f"## Claim\n{hyp.get('claim')}\n\n"
            "## Evidence\n" + "\n".join(f"- {e}" for e in hyp.get("evidence", [])) + "\n"
        )
        return engine._proposal(
            source_task_id="mine_patterns",
            proposal_type="failure_pattern",
            title=f"Record failure pattern `{title}`",
            reason="Mined from cross-run episodes.",
            risk_level="low",
            evidence=[f"Evidence count: {hyp.get('evidence_count')}"],
            confidence=hyp.get("confidence", 0.6),
            affected_files=[],
            trigger_reason="Pattern miner found repeating failure signatures.",
            future_applicability="Avoid matching failures.",
            applicability_scope="Avoid matching failures.",
            changes=[{"path": f"experience/failures/{title}.md", "operation": "write", "content": content}]
        )

    @staticmethod
    def _skill_refinement(hyp: dict[str, Any], engine: Any) -> dict[str, Any] | None:
        # Stub for skill refinement
        return None


def _as_lines(value: Any, *, fallback: list[str], code: bool = False) -> list[str]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    else:
        items = [value]
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("summary") or item.get("reason") or item.get("episode_id") or item.get("task_id")
        else:
            text = item
        text = str(text or "").strip()
        if not text:
            continue
        if code and not (text.startswith("`") and text.endswith("`")) and text != fallback[0]:
            text = f"`{text}`"
        if text not in lines:
            lines.append(text)
    return lines or fallback


def _counterexample_lines(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            episode = item.get("episode_id") or item.get("task_id") or "unknown episode"
            reason = item.get("reason") or "counterexample recorded"
            lines.append(f"`{episode}`: {reason}")
        elif item:
            lines.append(str(item))
    return lines or ["None recorded yet. Treat this as absence of known counterexamples, not proof."]


def _source_episode_lines(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            episode_id = item.get("episode_id") or "unknown"
            task_id = item.get("task_id") or "unknown"
            parts = [f"`{episode_id}` from task `{task_id}`"]
            if item.get("failure_signature"):
                parts.append(f"signature `{item['failure_signature']}`")
            if item.get("symptom"):
                parts.append(str(item["symptom"]))
            if item.get("fix_pattern"):
                parts.append(f"fix `{item['fix_pattern']}`")
            verification = item.get("verification_commands") or []
            if verification:
                parts.append("verified by " + ", ".join(f"`{cmd}`" for cmd in verification[:2]))
            lines.append("; ".join(parts))
        elif item:
            lines.append(f"`{item}`")
    return lines or ["No source episode details recorded."]


def _bullet_block(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines)
