from __future__ import annotations

from typing import Any

from .utils import new_id


class EpisodeBuilder:
    """
    Builds learning episodes from RunEvidence.
    """

    @staticmethod
    def build(evidence: dict[str, Any]) -> list[dict[str, Any]]:
        episodes: list[dict[str, Any]] = []

        task_id = evidence.get("task_id", "unknown")
        touched_files = evidence.get("touched_files", [])
        read_files = evidence.get("read_files", [])
        failure_signatures = evidence.get("failure_signatures", [])
        failure_excerpts = evidence.get("failure_excerpts", [])
        fix_actions = evidence.get("fix_actions", [])
        passed_commands = evidence.get("verification_commands") or evidence.get("passed_commands", [])
        failed_commands = evidence.get("failed_commands", [])
        blocked_actions = evidence.get("blocked_actions", [])
        loaded_assets = evidence.get("loaded_assets", [])
        reward = evidence.get("reward", {}) if isinstance(evidence.get("reward"), dict) else {}
        outcome = evidence.get("outcome", "unknown")
        outcome_quality = _episode_outcome_quality(outcome, reward)
        accepted_proposals = _proposal_summaries(evidence.get("accepted_proposals", []))
        rejected_proposals = _proposal_summaries(evidence.get("rejected_proposals", []))
        user_feedback = _user_feedback_summaries(evidence.get("user_feedback", []))
        
        # 1. test_failure_repair
        # If there's a failure signature and touched files, and the outcome is success or tests passed.
        if failure_signatures and touched_files and evidence.get("outcome") in {"completed", "success"}:
            for signature in failure_signatures:
                applies_when = _applies_when(
                    signature=signature,
                    touched_files=touched_files,
                    failed_commands=failed_commands,
                    loaded_assets=loaded_assets,
                )
                does_not_apply_when = _does_not_apply_when(
                    touched_files=touched_files,
                    verification_commands=passed_commands,
                )
                fix_strategy = _fix_strategy(fix_actions)
                evidence_lines = _evidence_lines(
                    task_id=task_id,
                    signature=signature,
                    touched_files=touched_files,
                    failed_commands=failed_commands,
                    verification_commands=passed_commands,
                    failure_excerpts=failure_excerpts,
                    fix_actions=fix_actions,
                )
                episodes.append({
                    "episode_id": new_id("ep"),
                    "task_id": task_id,
                    "category": "test_failure_repair",
                    "failure_signature": signature,
                    "symptom": f"Encountered {signature}",
                    "root_cause": "Needs further cross-run pattern mining to determine",
                    "fix_pattern": "; ".join(str(f.get("summary") or f.get("file")) for f in fix_actions),
                    "fix_strategy": fix_strategy,
                    "verification": passed_commands,
                    "verification_commands": passed_commands,
                    "affected_files": touched_files,
                    "fix_actions": fix_actions,
                    "loaded_assets": loaded_assets,
                    "outcome": outcome_quality,
                    "accepted_proposals": accepted_proposals,
                    "rejected_proposals": rejected_proposals,
                    "user_feedback": user_feedback,
                    "applies_when": applies_when,
                    "does_not_apply_when": does_not_apply_when,
                    "evidence": evidence_lines,
                    "failure_excerpts": failure_excerpts,
                    "touched_files": touched_files,
                    "read_files": read_files,
                    "scope": {
                        "applies_to": touched_files,
                        "does_not_apply_to": does_not_apply_when,
                    },
                    "evidence_refs": [task_id],
                    "confidence": "medium",
                    "confidence_score": 0.68,
                    "expected_future_use": (
                        "Load this episode when a future task reproduces the same failure signature, "
                        "touches the same files, and can be verified with the recorded commands."
                    ),
                })

        # 2. shell_resource_issue
        # Check for blocked actions or timeout
        if blocked_actions:
            blocked_types = [a.get("action_type") for a in blocked_actions if a.get("action_type")]
            episodes.append({
                "episode_id": new_id("ep"),
                "task_id": task_id,
                "category": "shell_resource_issue",
                "failure_signature": "blocked_action",
                "symptom": "Action blocked by safety policy",
                "root_cause": "Command matches forbidden pattern or timeout",
                "fix_pattern": "Bypass or use safe alternatives",
                "fix_strategy": [
                    "Prefer an allowed command prefix or a narrower verification command.",
                    "Do not learn the blocked command as a normal project workflow.",
                ],
                "verification": [],
                "verification_commands": [],
                "affected_files": [],
                "fix_actions": [],
                "loaded_assets": loaded_assets,
                "outcome": outcome_quality,
                "accepted_proposals": accepted_proposals,
                "rejected_proposals": rejected_proposals,
                "user_feedback": user_feedback,
                "applies_when": [f"A future action is blocked with action type `{item}`." for item in blocked_types]
                or ["A future shell or file action is blocked by the same safety policy."],
                "does_not_apply_when": ["The command is explicitly allowlisted and has no sensitive path or destructive token."],
                "evidence": [f"Task `{task_id}` recorded {len(blocked_actions)} blocked action(s)."],
                "scope": {
                    "applies_to": blocked_types,
                    "does_not_apply_to": ["safe allowlisted commands"],
                },
                "evidence_refs": [task_id],
                "confidence": "medium",
                "confidence_score": 0.62,
                "expected_future_use": "Use as a guardrail before retrying similar shell/resource actions.",
            })

        # 3. asset_lifecycle_change
        # If intent contains merge, archive, deprecate, rewrite, consolidate
        intent = evidence.get("intent", "").lower()
        if any(w in intent for w in ["merge", "archive", "deprecate", "rewrite", "consolidate", "合并", "归档"]):
            episodes.append({
                "episode_id": new_id("ep"),
                "task_id": task_id,
                "category": "asset_lifecycle_change",
                "failure_signature": "n/a",
                "symptom": "Duplicate or outdated assets",
                "root_cause": "Asset lifecycle management requested",
                "fix_pattern": "Consolidate or update metadata status",
                "fix_strategy": ["Compare duplicate assets, preserve source evidence, and apply lifecycle metadata by proposal."],
                "verification": passed_commands,
                "verification_commands": passed_commands,
                "affected_files": touched_files,
                "fix_actions": fix_actions,
                "loaded_assets": loaded_assets,
                "outcome": outcome_quality,
                "accepted_proposals": accepted_proposals,
                "rejected_proposals": rejected_proposals,
                "user_feedback": user_feedback,
                "applies_when": ["A future task asks to merge, archive, deprecate, rewrite, or consolidate experience assets."],
                "does_not_apply_when": ["The task is a direct code repair with no durable asset lifecycle change."],
                "evidence": [f"Task `{task_id}` intent requested asset lifecycle work."],
                "scope": {
                    "applies_to": ["Asset Governance"],
                    "does_not_apply_to": ["Code repair"]
                },
                "evidence_refs": [task_id],
                "confidence": "high",
                "confidence_score": 0.82,
                "expected_future_use": "Use when consolidating noisy memories, skills, failures, or project patterns.",
            })

        return episodes


def _applies_when(
    *,
    signature: str,
    touched_files: list[str],
    failed_commands: list[str],
    loaded_assets: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    if signature:
        lines.append(f"A future run observes `{signature}` or a close failure signature.")
    for path in touched_files[:4]:
        lines.append(f"The task touches `{path}` or the same owning module.")
    for command in failed_commands[:2]:
        lines.append(f"The failure appears while running `{command}`.")
    for asset in loaded_assets[:2]:
        path = asset.get("path") if isinstance(asset, dict) else None
        if path:
            lines.append(f"The same project experience asset was relevant: `{path}`.")
    return _unique(lines) or ["A future task matches the same failure, files, and verification context."]


def _does_not_apply_when(*, touched_files: list[str], verification_commands: list[str]) -> list[str]:
    lines = [
        "The future task has a different failure signature and no overlap with the touched files.",
        "The change crosses architecture, security, storage, or routing boundaries not represented by this episode.",
    ]
    if verification_commands:
        lines.append("The recorded verification command is irrelevant or cannot exercise the changed behavior.")
    if touched_files:
        lines.append("Only unrelated modules are affected.")
    return _unique(lines)


def _fix_strategy(fix_actions: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for fix in fix_actions[:6]:
        summary = fix.get("summary") or fix.get("file")
        if not summary:
            continue
        lines.append(str(summary))
        diff_excerpt = str(fix.get("diff_excerpt") or "").strip()
        if diff_excerpt:
            lines.append(f"Use the recorded diff shape as evidence, not as a blind patch: {diff_excerpt.splitlines()[0]}")
    return _unique(lines) or ["Inspect the failing behavior, make the smallest scoped repair, then run the recorded verification."]


def _evidence_lines(
    *,
    task_id: str,
    signature: str,
    touched_files: list[str],
    failed_commands: list[str],
    verification_commands: list[str],
    failure_excerpts: list[str],
    fix_actions: list[dict[str, Any]],
) -> list[str]:
    lines = [f"Source task `{task_id}` produced failure signature `{signature}`."]
    if touched_files:
        lines.append("Touched files: " + ", ".join(f"`{path}`" for path in touched_files[:6]) + ".")
    if failed_commands:
        lines.append("Failing command(s): " + ", ".join(f"`{command}`" for command in failed_commands[:3]) + ".")
    if verification_commands:
        lines.append("Verification command(s): " + ", ".join(f"`{command}`" for command in verification_commands[:3]) + ".")
    for excerpt in failure_excerpts[:2]:
        lines.append(f"Failure excerpt: `{str(excerpt).strip()[:180]}`")
    for fix in fix_actions[:3]:
        if fix.get("summary"):
            lines.append(f"Fix action: {fix['summary']}.")
    return _unique(lines)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _episode_outcome_quality(outcome: Any, reward: dict[str, Any]) -> str:
    text = str(outcome or "unknown").lower()
    if text in {"completed", "success"}:
        if reward.get("regression_passed") is False:
            return "mixed"
        return "success"
    if text in {"failed", "failure"}:
        return "negative"
    if text == "needs_human":
        return "needs_human"
    return "unknown"


def _proposal_summaries(values: Any) -> list[dict[str, Any]]:
    proposals = values if isinstance(values, list) else []
    result: list[dict[str, Any]] = []
    for item in proposals:
        if isinstance(item, dict):
            result.append(
                {
                    "proposal_id": item.get("proposal_id"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "reason": item.get("reason"),
                }
            )
        elif item:
            result.append({"proposal_id": str(item)})
    return result


def _user_feedback_summaries(values: Any) -> list[dict[str, Any]]:
    feedback = values if isinstance(values, list) else []
    result: list[dict[str, Any]] = []
    for item in feedback:
        if isinstance(item, dict):
            result.append(
                {
                    "feedback_id": item.get("feedback_id"),
                    "target_type": item.get("target_type"),
                    "target_id": item.get("target_id"),
                    "sentiment": item.get("sentiment"),
                    "strength": item.get("strength"),
                    "feedback_type": item.get("feedback_type"),
                    "raw_text": item.get("raw_text"),
                }
            )
    return result
