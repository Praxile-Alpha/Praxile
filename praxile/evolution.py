from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from typing import Any

from .config import Config
from .evidence import EvidenceExtractor
from .episodes import EpisodeBuilder
from .patterns import PatternMiner
from .json_utils import RobustJSONError, parse_json_value
from .model import ModelError, ModelUnavailable
from .silent_failure import apply_silent_failure_to_proposals
from .utils import new_id, slugify, unified_diff, utc_now


class EvolutionRouter(Protocol):
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str = "default",
        private: bool = False,
        high_risk: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        ...


class EvolutionEngine:
    def __init__(self, config: Config, router: EvolutionRouter | None = None):
        self.config = config
        self.paths = config.paths
        self.router = router

    def generate(self, trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        # Phase 1: Extract Evidence
        evidence = EvidenceExtractor.extract(trajectory)
        trajectory["evidence"] = evidence
        
        # Save evidence
        task_id = trajectory.get("task_id", "unknown")
        evidence_path = self.paths.state / "experience" / "evidence" / f"{task_id}.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")

        # Phase 2: Build Episodes
        episodes = EpisodeBuilder.build(evidence)
        trajectory["episodes"] = episodes
        
        # Save episodes
        for ep in episodes:
            ep_path = self.paths.state / "experience" / "episodes" / f"{ep['episode_id']}.json"
            ep_path.parent.mkdir(parents=True, exist_ok=True)
            ep_path.write_text(json.dumps(ep, indent=2, ensure_ascii=False), encoding="utf-8")

        # Phase 3: Pattern Mining
        # Update the pattern index based on all historical episodes + new ones
        patterns = PatternMiner.update_index(self.paths.state, config=self.config, router=self.router)
        trajectory["mined_patterns"] = patterns

        report = trajectory.get("reward_report", {}) or {}
        generation = report.get("experience_generation", {}) or {}
        should_generate = bool(report.get("should_generate_experience", generation.get("should_generate_experience", True)))
        if not should_generate:
            trajectory.setdefault("experience_generation", generation)
            return []
        proposals: list[dict[str, Any]] = []
        if self._should_generate_memory_proposal(trajectory):
            proposals.append(self._memory_proposal(trajectory))
        skill = self._skill_proposal(trajectory)
        if skill:
            proposals.append(skill)
        eval_case = self._eval_proposal(trajectory)
        if eval_case:
            proposals.append(eval_case)
        failure = self._failure_pattern_proposal(trajectory)
        if failure:
            proposals.append(failure)
        frozen = self._frozen_boundary_proposal(trajectory)
        if frozen:
            proposals.append(frozen)
        gate = self._architecture_gate_proposal(trajectory)
        if gate:
            proposals.append(gate)
        harness_rule = self._harness_rule_proposal(trajectory)
        if harness_rule:
            proposals.append(harness_rule)
        routing = self._routing_proposal(trajectory)
        if routing:
            proposals.append(routing)
        proposals.extend(self._llm_assisted_proposals(trajectory))
        self._apply_llm_judge_to_proposals(proposals, trajectory.get("llm_judge_reward", {}))
        apply_silent_failure_to_proposals(proposals, trajectory.get("silent_failure_signals") or [])
        filtered = self._filter_suppressed_proposals(proposals)
        return self._apply_proposal_gate(filtered, trajectory)

    def _should_generate_memory_proposal(self, trajectory: dict[str, Any]) -> bool:
        report = trajectory.get("reward_report", {}) or {}
        signals = (report.get("experience_generation") or {}).get("signals") or {}
        if signals.get("memory_requested"):
            return True
        if signals.get("failures") or signals.get("blocked_actions") or signals.get("architecture_gate"):
            return True
        if signals.get("model_performance"):
            return True
        if trajectory.get("spec_context", {}).get("spec_files"):
            return True
        if _failed_commands(trajectory) or _verification_commands(trajectory):
            return True
        return False

    def _apply_llm_judge_to_proposals(self, proposals: list[dict[str, Any]], judge: dict[str, Any]) -> None:
        if not isinstance(judge, dict) or not judge.get("active"):
            return
        judge_summary = {
            "model_role": judge.get("model_role"),
            "provider": judge.get("provider"),
            "model": judge.get("model"),
            "specificity": judge.get("specificity"),
            "evidence_quality": judge.get("evidence_quality"),
            "scope_control": judge.get("scope_control"),
            "intent_alignment": judge.get("intent_alignment"),
            "overgeneralization_risk": judge.get("overgeneralization_risk"),
            "recommended_action": judge.get("recommended_action"),
            "score": judge.get("score"),
            "reasons": judge.get("reasons") or judge.get("notes") or [],
        }
        try:
            overgeneralization_risk = float(judge_summary.get("overgeneralization_risk") or 0.0)
        except (TypeError, ValueError):
            overgeneralization_risk = 0.0
        for proposal in proposals:
            if proposal.get("type") in {"architecture_gate", "frozen_boundary", "routing"}:
                continue
            proposal["llm_judge"] = judge_summary
            if overgeneralization_risk >= 0.65:
                proposal["confidence"] = max(0.1, round(float(proposal.get("confidence") or 0.5) - 0.10, 3))
                proposal["confidence_level"] = _confidence_level(proposal["confidence"])
                proposal["recommended_action_override"] = "reject_or_edit"
                proposal.setdefault("feedback_influence", []).append(
                    {
                        "type": "llm_judge_overgeneralization",
                        "effect": "lowered_confidence_and_recommended_reject_or_edit",
                        "overgeneralization_risk": overgeneralization_risk,
                    }
                )

    def _filter_suppressed_proposals(self, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rejected = _recent_rejected_proposals(self.paths.proposals_rejected, limit=80)
        if not rejected:
            return proposals
        threshold = int(self.config.get("evolution", "rejection_suppression_threshold", default=2) or 2)
        threshold = max(1, threshold)
        filtered: list[dict[str, Any]] = []
        for proposal in proposals:
            proposal_type = str(proposal.get("type") or "")
            if proposal_type in {"architecture_gate", "frozen_boundary", "routing"}:
                filtered.append(proposal)
                continue
            matches = _matching_rejections(proposal, rejected)
            low_confidence = proposal.get("confidence_level") == "low" or float(proposal.get("confidence") or 0) < 0.5
            required = max(1, threshold - 1) if low_confidence else threshold
            if len(matches) >= required:
                continue
            if matches:
                proposal.setdefault("feedback_influence", []).append(
                    {
                        "type": "rejected_similarity",
                        "matching_rejections": len(matches),
                        "effect": "kept_but_review_should_inspect_specificity",
                    }
                )
            filtered.append(proposal)
        return filtered

    def _apply_proposal_gate(self, proposals: list[dict[str, Any]], trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.config.get("proposal_gate", "enabled", default=True):
            trajectory["proposal_gate_summary"] = {
                "generated": len(proposals),
                "pending": len(proposals),
                "suppressed": 0,
                "disabled": True,
                "constitution_files": trajectory.get("spec_context", {}).get("constitution_files") or [],
            }
            return proposals
        passed: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        for proposal in proposals:
            gate = self._proposal_gate_decision(proposal, trajectory)
            proposal["proposal_gate"] = gate
            if gate["passed"]:
                passed.append(proposal)
            else:
                weak = {
                    "proposal_id": proposal.get("proposal_id"),
                    "type": proposal.get("type"),
                    "title": proposal.get("title"),
                    "confidence": proposal.get("confidence"),
                    "confidence_level": proposal.get("confidence_level"),
                    "proposal_gate": gate,
                    "target_files": proposal.get("target_files") or [],
                }
                suppressed.append(weak)
        trajectory["proposal_gate_summary"] = {
            "generated": len(proposals),
            "pending": len(passed),
            "suppressed": len(suppressed),
            "constitution_files": trajectory.get("spec_context", {}).get("constitution_files") or [],
        }
        if suppressed:
            trajectory["suppressed_experience_candidates"] = suppressed
        return passed

    def _proposal_gate_decision(self, proposal: dict[str, Any], trajectory: dict[str, Any]) -> dict[str, Any]:
        reasons: list[str] = []
        suppressed_reasons: list[str] = []
        report = trajectory.get("reward_report", {}) or {}
        evidence_strength = str((report.get("experience_generation") or {}).get("evidence_strength") or "low")
        spec_compliance = trajectory.get("spec_compliance") if isinstance(trajectory.get("spec_compliance"), dict) else {}
        spec_status = str(spec_compliance.get("status") or "unknown")
        spec_violations = spec_compliance.get("violations") or []
        spec_missing = spec_compliance.get("missing") or []
        confidence = _float(proposal.get("confidence"), default=0.0)
        min_confidence = float(self.config.get("proposal_gate", "min_confidence", default=0.55) or 0.55)
        critical_types = {"architecture_gate", "frozen_boundary", "routing"}
        compliance_safe_types = {
            "eval_case",
            "eval_checklist",
            "failure_pattern",
            "harness_rule",
            "architecture_gate",
            "frozen_boundary",
            "routing",
            "experience_consolidation",
            "asset_deprecate",
            "asset_rewrite",
            "asset_archive",
            "asset_merge",
        }
        source_ok = bool(proposal.get("source_task_id") or proposal.get("source", {}).get("task_id"))
        evidence_ok = bool(proposal.get("evidence_summary") or proposal.get("evidence"))
        scope_ok = bool(proposal.get("applicability_scope") or proposal.get("future_applicability"))
        anti_scope_ok = bool(proposal.get("anti_scope"))
        rollback_ok = bool(proposal.get("target_files") or proposal.get("rollback"))

        if source_ok:
            reasons.append("Source task is recorded.")
        else:
            suppressed_reasons.append("Missing source_task_id.")
        if evidence_ok:
            reasons.append(f"Evidence strength is {evidence_strength}.")
        else:
            suppressed_reasons.append("Missing evidence summary.")
        if confidence >= min_confidence or proposal.get("type") in critical_types:
            reasons.append("Confidence is above threshold or proposal type is governance-critical.")
        else:
            suppressed_reasons.append(f"Confidence {confidence} is below threshold {min_confidence}.")
        if scope_ok:
            reasons.append("Applicability scope is present.")
        else:
            suppressed_reasons.append("Missing applicability scope.")
        if anti_scope_ok:
            reasons.append("Anti-scope is present.")
        else:
            suppressed_reasons.append("Missing anti-scope.")
        if rollback_ok:
            reasons.append("Target files or rollback path are present.")
        else:
            suppressed_reasons.append("Missing target files or rollback path.")

        weak_evidence = evidence_strength == "low" and proposal.get("type") not in critical_types
        if weak_evidence and confidence < 0.7:
            suppressed_reasons.append("Evidence is low and confidence is not strong enough.")
        if spec_compliance and spec_status in {"partial", "failed"} and proposal.get("type") not in compliance_safe_types:
            if spec_violations:
                suppressed_reasons.append("Spec compliance violations exist; do not persist implementation-derived experience as normal memory/skill.")
            elif spec_missing:
                suppressed_reasons.append("Spec acceptance criteria are missing; inspect before persisting implementation-derived experience.")
            else:
                suppressed_reasons.append("Spec compliance is incomplete; inspect before persisting implementation-derived experience.")
        if spec_compliance and spec_status == "full":
            reasons.append("Attached spec compliance is satisfied.")

        passed = not suppressed_reasons
        return {
            "passed": passed,
            "decision": "pending" if passed else "weak_candidate",
            "quality_score": round(min(1.0, confidence * 0.6 + (0.4 if evidence_ok and scope_ok and anti_scope_ok else 0.0)), 3),
            "evidence_strength": evidence_strength,
            "scope_valid": scope_ok,
            "anti_scope_valid": anti_scope_ok,
            "duplicate_risk": "unknown",
            "spec_compliance_status": spec_status if spec_compliance else None,
            "reasons": reasons,
            "suppressed_reasons": suppressed_reasons,
        }

    def _proposal(
        self,
        *,
        source_task_id: str,
        proposal_type: str,
        title: str,
        reason: str,
        risk_level: str,
        changes: list[dict[str, str]],
        evidence: list[str] | None = None,
        confidence: float = 0.6,
        affected_files: list[str] | None = None,
        trigger_reason: str | None = None,
        future_applicability: str | None = None,
        priority: str | None = None,
        applicability_scope: str | None = None,
        anti_scope: str | None = None,
        generated_by: str = "deterministic_evolution",
    ) -> dict[str, Any]:
        target_files = [change["path"] for change in changes]
        diffs: list[str] = []
        for change in changes:
            target = self._safe_diff_target(change["path"])
            if change.get("operation") == "metadata_update":
                target = target.with_name(f"{target.name}.meta.json")
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            if change.get("operation") == "append":
                after = before.rstrip() + "\n\n" + change.get("content", "").rstrip() + "\n"
            elif change.get("operation") == "metadata_update":
                current = {}
                if before.strip():
                    try:
                        current = json.loads(before)
                    except json.JSONDecodeError:
                        current = {}
                if not isinstance(current, dict):
                    current = {}
                current.update(change.get("metadata") or {})
                after = json.dumps(current, indent=2, ensure_ascii=False) + "\n"
            else:
                after = change.get("content", "")
            diffs.append(unified_diff(before, after, f"a/.praxile/{change['path']}", f"b/.praxile/{change['path']}"))
        evidence_values = evidence or [f"Generated from trajectory `{source_task_id}`."]
        normalized_risk = _normalized_risk(proposal_type, risk_level)
        confidence_level = _confidence_level(confidence)
        applicability = applicability_scope or future_applicability or "Apply only to future tasks similar to the source task after user review."
        return {
            "proposal_id": new_id("prop"),
            "source_task_id": source_task_id,
            "source_trajectory_id": source_task_id,
            "type": proposal_type,
            "title": title,
            "reason": reason,
            "target_files": target_files,
            "diff": "\n".join(diff for diff in diffs if diff),
            "risk_level": normalized_risk,
            "priority": priority or _proposal_priority(proposal_type, normalized_risk, confidence),
            "source": {
                "type": "trajectory",
                "task_id": source_task_id,
                "trajectory_id": source_task_id,
            },
            "evidence": evidence_values,
            "evidence_items": [
                {
                    "source": "trajectory",
                    "summary": item,
                }
                for item in evidence_values
            ],
            "evidence_summary": _summarize_evidence(evidence_values),
            "affected_files": affected_files or [],
            "trigger_reason": trigger_reason or reason,
            "confidence": confidence,
            "confidence_level": confidence_level,
            "future_applicability": future_applicability or applicability,
            "applicability_scope": applicability,
            "anti_scope": anti_scope
            or "Do not apply when the future task has different architecture, security, data, UX, or runtime constraints.",
            "requires_user_approval": True,
            "requires_manual_review": True,
            "status": "pending",
            "generated_by": generated_by,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "changes": changes,
        }

    def _safe_diff_target(self, change_path: str) -> Path:
        path = Path(change_path)
        parts = path.parts
        if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
            return self.paths.state / "__unsafe_proposal_path__"
        return self.paths.state.joinpath(*parts)

    def _memory_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        task_id = trajectory["task_id"]
        task = trajectory.get("user_task", "")
        report = trajectory.get("reward_report", {})
        notes = "; ".join(report.get("notes", [])[:3])
        edited_paths = _edited_paths(trajectory)
        failed_commands = _failed_commands(trajectory)
        verification_commands = _verification_commands(trajectory)
        failure_excerpts = _failure_excerpts(trajectory)
        project_terms = _project_terms(trajectory)
        executor_lines = _executor_evidence_lines(trajectory)
        evidence_lines = []
        if edited_paths:
            evidence_lines.append(f"- Touched files: {', '.join(f'`{path}`' for path in edited_paths[:8])}")
        if failed_commands:
            evidence_lines.append(f"- Failure command(s): {', '.join(f'`{command}`' for command in failed_commands[:4])}")
        if verification_commands:
            evidence_lines.append(f"- Verification command(s): {', '.join(f'`{command}`' for command in verification_commands[:4])}")
        if failure_excerpts:
            evidence_lines.append(f"- Failure excerpt: {failure_excerpts[0]}")
        if notes:
            evidence_lines.append(f"- Reward signals: {notes}")
        if executor_lines:
            evidence_lines.append(f"- Executor attribution: {executor_lines[0].lstrip('- ')}")
        if not evidence_lines:
            evidence_lines.append("- Evidence: source task explicitly requested project-local memory.")
        content = (
            f"## Task {task_id}\n\n"
            f"- Source: `{task_id}`\n"
            f"- Task: {task}\n"
            f"- Result: {trajectory.get('result', {}).get('status', 'unknown')}\n"
            f"- Reward overall: {report.get('overall', 'n/a')}\n"
            "\n### applies_when\n\n"
            + "\n".join(_applies_when_lines(project_terms, edited_paths, failed_commands, verification_commands))
            + "\n\n### does_not_apply_when\n\n"
            "- The future task touches unrelated files, commands, frameworks, or architecture boundaries.\n"
            "- The failure signature, verification command, or changed module does not match this source run.\n"
            "- A newer accepted memory, skill, or failure pattern explicitly supersedes this note.\n"
            "\n### Concrete Evidence\n\n"
            + "\n".join(evidence_lines)
            + "\n\n### Executor Attribution\n\n"
            + ("\n".join(executor_lines) if executor_lines else "- Executor attribution was not recorded in this source trajectory.")
            + "\n\n### Next-Time Use\n\n"
            "- Load this memory only for similar project-local tasks with matching files, commands, tests, or failure signatures.\n"
            "- Reproduce the narrow failure or verification command before broad edits when the task is a repair.\n"
            "- Keep durable memory/skill/eval/rule updates proposal-only until reviewed.\n"
        )
        route = trajectory.get("model_routing", {}).get("selected")
        if route:
            content += f"- Model route: `{route.get('target')}` for `{route.get('purpose')}`; reasons: {', '.join(route.get('reasons', []))}\n"
        return self._proposal(
            source_task_id=task_id,
            proposal_type="memory_update",
            title=_project_specific_title("Record project experience", trajectory),
            reason="Every completed task should leave a source-linked memory candidate, but only after user approval.",
            risk_level="low",
            evidence=[
                f"Reward overall: {report.get('overall', 'n/a')}.",
                f"Result status: {trajectory.get('result', {}).get('status', 'unknown')}.",
                f"Experience generation: {report.get('experience_generation', {}).get('reason', 'not recorded')}.",
                "The memory is scoped to this project and keeps the source task ID.",
                *(executor_lines[:2] or []),
            ],
            confidence=0.55 if trajectory.get("result", {}).get("status") == "needs_human" else 0.7,
            affected_files=edited_paths,
            trigger_reason="Reward report indicated this run has project-local experience value.",
            future_applicability="Project memory for similar local code tasks; do not treat as a global fact.",
            applicability_scope="Project memory for similar local code tasks; do not treat as a global fact.",
            anti_scope="Do not use as user-global preference, external framework memory, or evidence for unrelated repositories.",
            changes=[{"path": self._memory_target_path(), "operation": "append", "content": content}],
        )

    def _memory_target_path(self) -> str:
        if not self.config.get("memory", "shard_enabled", default=True):
            return "memory/project.md"
        soft_limit = int(self.config.get("memory", "project_memory_soft_limit_bytes", default=200000) or 200000)
        project_memory = self.paths.state / "memory" / "project.md"
        try:
            size = project_memory.stat().st_size
        except OSError:
            size = 0
        if size < soft_limit:
            return "memory/project.md"
        stamp = utc_now()[:7]
        return f"memory/shards/{stamp}.md"

    def _skill_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        task = trajectory.get("user_task", "").lower()
        result = trajectory.get("result", {}).get("status")
        if result not in {"completed", "needs_human"}:
            return None
        if any(word in task for word in ["ui", "button", "页面", "按钮", "交互", "导航", "选中态", "layout"]):
            project_slug = _skill_context_slug(trajectory, "ui-interaction-review")
            name = project_slug
            description = "Use when modifying this project's UI interactions, navigation, selected states, visual feedback, or user-facing workflows."
            checklist = [
                "Confirm the interaction is reachable from the intended entry point.",
                "Verify selected, hover, focus, disabled, loading, and error states where relevant.",
                "Check desktop and mobile layouts for text overlap and visible affordances.",
                "Run automated tests when available and keep a human visual acceptance item.",
            ]
            tags = "[ui, frontend, interaction, review]"
        elif any(word in task for word in ["test", "pytest", "lint", "build", "测试", "构建"]):
            project_slug = _skill_context_slug(trajectory, "test-failure-repair")
            name = project_slug
            description = "Use when repairing this project's failing tests, lint errors, or build regressions with matching files or commands."
            checklist = [
                "Reproduce the failure before editing when possible.",
                "Prefer the smallest code change that addresses the failing assertion or error.",
                "Run the narrow failing test first, then the broader configured command.",
                "Record the failure signature if the repair is reusable.",
            ]
            tags = "[testing, repair, regression]"
        else:
            return None

        skill_dir = Path("skills") / name
        version = "0.1.0"
        content = (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"version: {version}\n"
            "risk_level: low\n"
            f"tags: {tags}\n"
            f"source_task_id: {trajectory['task_id']}\n"
            "---\n\n"
            f"# {name}\n\n"
            "## Project Context\n\n"
            + "\n".join(_project_context_lines(trajectory))
            + "\n\n"
            "## Source Evidence\n\n"
            + "\n".join(_skill_source_evidence_lines(trajectory))
            + "\n\n"
            "## When To Use\n\n"
            f"{description}\n\n"
            "## Steps\n\n"
            "1. Load project memory and any frozen-boundary rules related to the touched area.\n"
            "2. Inspect the current implementation before editing.\n"
            "3. Make the smallest scoped change that satisfies the task.\n"
            "4. Run objective verification when configured.\n"
            "5. Leave an explicit human acceptance item for anything tests cannot prove.\n\n"
            "## Checklist\n\n"
            + "\n".join(f"- {item}" for item in checklist)
            + "\n\n## Common Failure Patterns\n\n"
            "- Treating a user-facing change as complete without checking visible feedback.\n"
            "- Expanding the patch into unrelated cleanup.\n"
            "- Writing durable memory or skill updates without approval.\n"
        )
        metadata = {
            "name": name,
            "version": version,
            "status": "active",
            "lifecycle": ["proposed", "accepted", "active", "deprecated", "replaced"],
            "description": description,
            "source_task_ids": [trajectory["task_id"]],
            "confidence": 0.68,
            "applicability_scope": f"Use only for tasks matching the `{name}` skill description.",
            "anti_scope": "Do not use as a broad frontend or testing rule outside matching task signals.",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="skill_create",
            title=f"Create skill `{name}`",
            reason="The task matches a repeatable workflow that should be available to future similar tasks.",
            risk_level="low",
            evidence=[
                f"Task text and trajectory evidence matched repeatable workflow signals for `{name}`.",
                f"Task analysis type: `{trajectory.get('task_analysis', {}).get('task_type', 'unknown')}`.",
                f"Project context: {', '.join(_project_terms(trajectory)[:6]) or 'not recorded'}.",
                "The proposed SKILL.md includes triggers, steps, checklist, and failure patterns.",
            ],
            confidence=0.68,
            affected_files=_edited_paths(trajectory),
            trigger_reason="Task analysis surfaced a repeatable workflow candidate.",
            future_applicability=f"Use only for tasks matching the `{name}` skill description.",
            applicability_scope=f"Use only for tasks matching the `{name}` skill description.",
            anti_scope="Do not auto-load for unrelated implementation, architecture, or data migration tasks.",
            changes=[
                {"path": (skill_dir / "SKILL.md").as_posix(), "operation": "write", "content": content},
                {
                    "path": (skill_dir / "metadata.json").as_posix(),
                    "operation": "write",
                    "content": json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                },
                {
                    "path": (skill_dir / "versions" / f"{version}.md").as_posix(),
                    "operation": "write",
                    "content": content,
                },
            ],
        )

    def _eval_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        task = trajectory.get("user_task", "").lower()
        if not any(word in task for word in ["ui", "button", "页面", "按钮", "交互", "导航", "选中态"]):
            return None
        name = "ui-interaction-checklist"
        content = (
            f"# UI Interaction Checklist\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "- Entry point is visible and reachable.\n"
            "- Click/tap behavior satisfies the requested workflow.\n"
            "- Selected, hover, focus, disabled, and loading states are visually distinct where relevant.\n"
            "- Mobile and desktop layouts have no overlapping text or hidden controls.\n"
            "- Automated checks are run if available; human visual review remains required.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="eval_case",
            title="Add UI interaction acceptance checklist",
            reason="UI interaction work needs human-visible feedback checks in addition to functional execution.",
            risk_level="low",
            evidence=[
                "Task matched UI/interaction keywords.",
                "Reward report marks UI-sensitive work as requiring human confirmation.",
            ],
            confidence=0.72,
            affected_files=_edited_paths(trajectory),
            trigger_reason="UI-sensitive task requires human acceptance evidence beyond tests.",
            future_applicability="UX-sensitive UI tasks involving controls, navigation, layout, selected state, or feedback.",
            applicability_scope="UX-sensitive UI tasks involving controls, navigation, layout, selected state, or feedback.",
            changes=[{"path": f"evals/checklists/{name}.md", "operation": "write", "content": content}],
        )

    def _failure_pattern_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        failed = [a for a in trajectory.get("actions", []) if a.get("status") in {"failure", "blocked"}]
        tests = trajectory.get("reward_report", {}).get("test_results", [])
        failed_tests = [item for item in tests if item.get("status") != "success"]
        if not failed and not failed_tests:
            return None
        title = slugify(trajectory.get("user_task", "failure-pattern"), max_length=48)
        failure_type = self._failure_type(trajectory, failed, failed_tests)
        details = []
        for action in failed[:4]:
            details.append(
                f"- Action `{action.get('action_type')}` ended as `{action.get('status')}`: "
                f"{action.get('observation', {}).get('output', '')[:300]}"
            )
        for test in failed_tests[:4]:
            details.append(f"- Command `{test.get('data', {}).get('command')}` failed: {test.get('output', '')[:300]}")
        detail_text = "\n".join(details) if details else "- Reward report recorded a failed or blocked action."
        executor_lines = _executor_evidence_lines(trajectory)
        first_failed = (failed[:1] or [{}])[0]
        first_test = (failed_tests[:1] or [{}])[0]
        trigger_command = first_test.get("data", {}).get("command") or first_failed.get("input", {}).get("command") or "(not recorded)"
        symptom = (
            first_test.get("output")
            or first_failed.get("observation", {}).get("output")
            or "Failed or blocked action recorded in trajectory."
        )
        verification_commands = _verification_commands(trajectory)
        signature = _failure_signature(symptom)
        affected_files = _edited_paths(trajectory) or _paths_from_actions(trajectory)
        fix_actions = _fix_action_lines(trajectory)
        content = (
            f"# Failure Pattern: {title}\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "## Metadata\n\n"
            f"- failure_type: `{failure_type}`\n"
            f"- failure_signature: `{signature}`\n"
            f"- reproduce_command: `{trigger_command}`\n"
            f"- affected_files: {', '.join(f'`{path}`' for path in affected_files[:8]) or '`(not recorded)`'}\n"
            "- status: `active`\n"
            "- confidence: `0.65`\n"
            f"- source_run: `{trajectory['task_id']}`\n"
            f"- applicability_scope: `Similar {failure_type.replace('_', ' ')} signals in this project.`\n"
            "- anti_scope: `Do not generalize to unrelated stacks or safety contexts without repeated evidence.`\n\n"
            "## applies_when\n\n"
            + "\n".join(_applies_when_lines(_project_terms(trajectory), affected_files, _failed_commands(trajectory), verification_commands))
            + "\n\n## does_not_apply_when\n\n"
            "- The failing command, failure signature, or affected module does not match this source run.\n"
            "- The future task is architecture-sensitive and should enter Architecture Gate instead.\n"
            "- A newer accepted failure pattern or skill supersedes this record.\n\n"
            "## Trigger\n\n"
            f"- command: `{trigger_command}`\n"
            f"- files: {', '.join(f'`{path}`' for path in affected_files[:8]) or '`(not recorded)`'}\n\n"
            "## Symptom\n\n"
            f"{symptom[:500]}\n\n"
            "## Signal Details\n\n"
            + detail_text
            + "\n\n## Executor Attribution\n\n"
            + ("\n".join(executor_lines) if executor_lines else "- Executor attribution was not recorded.")
            + "\n\n## Fix Strategy\n\n"
            "- Reproduce the failure with the narrowest safe command before broad edits.\n"
            "- Do not bypass safety blocks; either adjust configuration after review or ask for manual execution.\n"
            "- Keep the repair scoped to the failing file, command, or contract until broader validation is required.\n\n"
            "## fix_actions\n\n"
            + "\n".join(f"- {item}" for item in fix_actions)
            + "\n\n"
            "## Verification\n\n"
            + (
                "\n".join(f"- `{command}`" for command in verification_commands)
                if verification_commands
                else "- Run the narrow failing command first, then the configured broader verification command."
            )
            + "\n\n## Anti-Pattern\n\n"
            "- Do not learn from a failed run as a normal successful workflow without human review.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="failure_pattern",
            title=f"Record failure pattern `{title}`",
            reason="A failed or blocked action should become a reusable guardrail if the user accepts it.",
            risk_level="medium",
            evidence=details or ["Reward report recorded a failed or blocked action."],
            confidence=0.65,
            affected_files=_edited_paths(trajectory),
            trigger_reason="Trajectory contains failed or safety-blocked actions.",
            future_applicability="Similar failures or safety blocks in the same project; do not generalize without repeated evidence.",
            applicability_scope="Similar failures or safety blocks in the same project; do not generalize without repeated evidence.",
            anti_scope="Do not apply to unrelated commands, stacks, or permissions without new objective evidence.",
            changes=[{"path": f"experience/failures/{title}.md", "operation": "write", "content": content}],
        )

    def _failure_type(
        self,
        trajectory: dict[str, Any],
        failed_actions: list[dict[str, Any]],
        failed_tests: list[dict[str, Any]],
    ) -> str:
        if any(action.get("status") == "blocked" for action in failed_actions):
            return "safety_failure"
        if any(action.get("action_type") == "architecture_gate" for action in failed_actions):
            return "architecture_failure"
        task = trajectory.get("user_task", "").lower()
        if any(word in task for word in ["ui", "ux", "button", "页面", "按钮", "交互", "选中态"]):
            return "ux_failure"
        if failed_tests:
            return "regression_failure"
        if any(action.get("action_type") == "model_response" for action in failed_actions):
            return "model_failure"
        if any(action.get("action_type") == "run_command" for action in failed_actions):
            return "environment_failure"
        return "task_failure"

    def _frozen_boundary_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        task = trajectory.get("user_task", "").lower()
        architecture_words = [
            "architecture",
            "schema",
            "contract",
            "auth",
            "permission",
            "routing",
            "database",
            "migration",
            "shared",
            "架构",
            "数据结构",
            "认证",
            "权限",
            "路由",
            "存储",
            "共享",
        ]
        edited_paths = [
            action.get("input", {}).get("path", "")
            for action in trajectory.get("actions", [])
            if action.get("action_type") == "edit_file" and action.get("status") == "success"
        ]
        if not (any(word in task for word in architecture_words) or len({Path(path).parts[0] for path in edited_paths if path}) >= 3):
            return None
        name = slugify(trajectory.get("user_task", "architecture-boundary"), max_length=48)
        content = (
            f"# Frozen Boundary Proposal: {name}\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "## Current Definition\n\n"
            "This task touched or described architecture-sensitive behavior. Treat future changes in this area as "
            "architecture-affecting until the boundary is edited or removed.\n\n"
            "## Affects\n\n"
            + ("\n".join(f"- `{path}`" for path in edited_paths) if edited_paths else "- Project-level shared behavior\n")
            + "\n\n## What Can Vary Locally\n\n"
            "- Local implementation details that do not alter the shared contract.\n\n"
            "## Reopen Review When\n\n"
            "- Shared state shape, cross-module interface, auth/session, routing, persistence, or migration behavior changes.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="frozen_boundary",
            title=f"Add frozen boundary `{name}`",
            reason="The task appears architecture-affecting and should trigger a gate in similar future work.",
            risk_level="high",
            evidence=[
                "Task matched architecture-sensitive keywords or edited multiple top-level areas.",
                "Frozen boundary keeps future shared-contract changes behind architecture review.",
            ],
            confidence=0.58 if not edited_paths else 0.7,
            affected_files=edited_paths,
            trigger_reason="Architecture-sensitive task signals or multi-area edits suggest a reusable boundary.",
            future_applicability="Future changes to the same shared contract, state flow, auth/session, routing, storage, or migration area.",
            applicability_scope="Future changes to the same shared contract, state flow, auth/session, routing, storage, or migration area.",
            anti_scope="Do not freeze unrelated local UI, copy, tests, or one-off implementation details.",
            changes=[{"path": f"rules/frozen-boundaries/{name}.md", "operation": "write", "content": content}],
        )

    def _architecture_gate_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        gate_actions = [a for a in trajectory.get("actions", []) if a.get("action_type") == "architecture_gate"]
        if not gate_actions:
            return None
        name = slugify(trajectory.get("user_task", "architecture-gate"), max_length=48)
        analysis = gate_actions[0].get("observation", {}).get("data", {})
        frozen_hits = analysis.get("frozen_hits", [])
        boundaries = "\n".join(f"- `{item.get('path')}`" for item in frozen_hits) or "- No accepted frozen boundary matched yet."
        content = (
            f"# Architecture Gate: {name}\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "## Trigger\n\n"
            "This task matched architecture-sensitive terms or accepted frozen boundaries, so normal implementation "
            "was paused before file edits.\n\n"
            "## Impact Scope\n\n"
            "- Shared contracts, state flow, auth/session, routing, persistence, or migration behavior may be affected.\n"
            f"- Retrieved boundaries:\n{boundaries}\n\n"
            "## Required Before Editing\n\n"
            "1. Define the root problem and why a local patch is insufficient.\n"
            "2. List affected modules, states, interfaces, and data flows.\n"
            "3. Compare alternatives and choose the smallest migration path.\n"
            "4. Define rollback steps and validation strategy.\n"
            "5. Decide whether a frozen boundary should be added or updated.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="architecture_gate",
            title=f"Record architecture gate `{name}`",
            reason="Architecture-sensitive work should leave a reusable gate record before implementation proceeds.",
            risk_level="high",
            evidence=[
                "Runtime triggered `architecture_gate` and did not edit files.",
                "Gate proposal lists impact scope, alternatives, migration, rollback, and validation requirements.",
            ],
            confidence=0.75,
            affected_files=_edited_paths(trajectory),
            trigger_reason="Runtime hard-stopped implementation through the Architecture Gate.",
            future_applicability="Architecture-sensitive tasks touching shared contracts, auth/session, routing, storage, migrations, or frozen boundaries.",
            applicability_scope="Architecture-sensitive tasks touching shared contracts, auth/session, routing, storage, migrations, or frozen boundaries.",
            anti_scope="Do not use this gate to block clearly local edits that avoid shared contracts and accepted frozen boundaries.",
            changes=[{"path": f"rules/architecture-gates/{name}.md", "operation": "write", "content": content}],
        )

    def _harness_rule_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        task = trajectory.get("user_task", "").lower()
        report = trajectory.get("reward_report", {})
        objective = report.get("objective_signals", {})
        executor_attribution = objective.get("executor_attribution") if isinstance(objective.get("executor_attribution"), dict) else {}
        parallel = executor_attribution.get("parallel_readonly") if isinstance(executor_attribution.get("parallel_readonly"), dict) else {}
        detected_tests = objective.get("tests_detected") or []
        tests_run = objective.get("tests_run")
        ui_sensitive = report.get("signals", {}).get("ui_sensitive")
        architecture_sensitive = report.get("signals", {}).get("architecture_sensitive")
        failed_or_blocked = objective.get("blocked_actions", 0) or objective.get("failed_actions", 0)
        parallel_readonly_issue = bool(parallel.get("failed_observation_count") or parallel.get("blocked_observation_count"))
        rules: list[str] = []
        name = "task-execution-guardrails"
        title = "Add task execution guardrail"
        if ui_sensitive:
            name = "ui-human-acceptance"
            title = "Require human acceptance for UI-sensitive tasks"
            rules.extend(
                [
                    "For UI/UX interaction tasks, include a human acceptance checklist even when automated tests pass.",
                    "When a Browser/Playwright adapter is available, capture screenshot or interaction evidence.",
                    "Do not mark UI feedback complete solely because a click path works.",
                ]
            )
        if detected_tests and not tests_run:
            name = "verification-before-learning"
            title = "Run detected verification before learning from code tasks"
            rules.append(
                "When project test/lint/build commands are detected, run an approved verification command before treating a code task as completed."
            )
        if architecture_sensitive:
            name = "architecture-gate-before-edits"
            title = "Force architecture gate before high-risk edits"
            rules.extend(
                [
                    "Tasks touching shared contracts, auth/session, routing, storage, migrations, or frozen boundaries must pause normal implementation.",
                    "High-risk implementation requires a strong model route and explicit human approval before edits.",
                ]
            )
        if parallel_readonly_issue:
            name = "parallel-readonly-context-integrity"
            title = "Require context review after failed parallel exploration"
            rules.extend(
                [
                    "If parallel read-only exploration has failed or blocked sub-observations, do not treat missing search results as proof that context does not exist.",
                    "Inspect failed explorer outputs before accepting memory, skill, rule, or pattern proposals from the run.",
                    "Prefer a narrower follow-up exploration or manual review before broad implementation.",
                ]
            )
        if failed_or_blocked:
            name = "stop-after-safety-or-repeat-failure"
            title = "Stop automatic attempts after safety blocks or repeated failures"
            rules.append(
                "If a task hits a safety block or repeated failed action, stop automatic attempts and generate a failure-pattern proposal."
            )
        if not rules:
            return None
        content = (
            f"# Harness Rule: {name}\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "## Rule\n\n"
            + "\n".join(f"- {rule}" for rule in rules)
            + "\n\n## Why\n\n"
            "This rule changes Agent Runtime behavior rather than project facts. It should only become active after review.\n\n"
            "## Audit Requirement\n\n"
            "- Keep the source task ID and accepted proposal record.\n"
            "- Roll back through `praxile rollback <proposal_id>` if the rule proves harmful.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="harness_rule",
            title=title,
            reason="The run exposed a reusable execution policy that belongs in harness rules, not memory or skill text.",
            risk_level="medium" if architecture_sensitive or failed_or_blocked else "low",
            evidence=rules,
            confidence=0.66,
            affected_files=_edited_paths(trajectory),
            trigger_reason="Reward signals indicated a reusable harness-level execution policy.",
            future_applicability="Agent runtime behavior for matching future tasks; user should edit or reject if too broad.",
            applicability_scope="Agent runtime behavior for matching future tasks; user should edit or reject if too broad.",
            anti_scope="Do not apply to unrelated task classes or use as permission to bypass safety checks.",
            changes=[{"path": f"rules/harness-rules/{name}.md", "operation": "write", "content": content}],
        )

    def _routing_proposal(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        routing = trajectory.get("model_routing", {})
        selected = routing.get("selected") or {}
        performance = routing.get("performance") or []
        if not selected and not performance:
            return None
        needs_routing_review = any(item.get("status") in {"unavailable", "invalid_action"} for item in performance)
        if selected.get("high_risk") or selected.get("private"):
            needs_routing_review = True
        if not needs_routing_review:
            return None
        name = slugify(f"model-routing-{selected.get('purpose', 'task')}", max_length=48)
        perf_lines = "\n".join(
            f"- `{item.get('status')}`: {item.get('failure_pattern', 'model signal')} {item.get('details', '')[:200]}"
            for item in performance
        ) or "- No failure recorded; proposal is based on privacy/high-risk routing policy."
        content = (
            f"# Model Routing Proposal: {name}\n\n"
            f"Source task: `{trajectory['task_id']}`\n\n"
            "## Selected Route\n\n"
            f"- Purpose: `{selected.get('purpose')}`\n"
            f"- Provider/model: `{selected.get('target')}`\n"
            f"- Private: `{selected.get('private')}`\n"
            f"- High risk: `{selected.get('high_risk')}`\n"
            f"- Reasons: {', '.join(selected.get('reasons', []))}\n\n"
            "## Performance Signals\n\n"
            f"{perf_lines}\n\n"
            "## Suggested Policy\n\n"
            "- Keep privacy-sensitive tasks on the private/local route when configured.\n"
            "- Use the coding/strong route for architecture-sensitive tasks and require human approval.\n"
            "- Use a cheaper/evolution route for summaries and experience extraction when objective signals are sufficient.\n"
            "- If a configured route is unavailable, propose a config review instead of silently falling back.\n"
        )
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type="routing",
            title=f"Review model routing for `{selected.get('purpose', 'task')}`",
            reason="Model route selection and failures are experience signals that can evolve future routing policy.",
            risk_level="high" if selected.get("high_risk") else "medium",
            evidence=[
                f"Selected route: `{selected.get('target')}`.",
                f"Private: `{selected.get('private')}`, high risk: `{selected.get('high_risk')}`.",
                "Model performance signals were recorded in the trajectory.",
            ],
            confidence=0.62,
            affected_files=[],
            trigger_reason="Model route privacy/high-risk status or failure performance requires review.",
            future_applicability="Model routing policy for similar privacy-sensitive, high-risk, or unavailable-route tasks.",
            applicability_scope="Model routing policy for similar privacy-sensitive, high-risk, or unavailable-route tasks.",
            anti_scope="Do not silently mutate model configuration or route unrelated low-risk tasks to expensive models.",
            changes=[{"path": f"rules/harness-rules/{name}.md", "operation": "write", "content": content}],
        )

    def _llm_assisted_proposals(self, trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.config.get("evolution", "llm_assisted_proposals", default=False):
            return []
        if self.router is None:
            return []
        messages = self._llm_evolution_messages(trajectory)
        analysis = trajectory.get("task_analysis", {})
        purpose = str(self.config.get("evolution", "llm_model_role", default="proposal_composer"))
        try:
            response = self.router.chat(
                messages,
                purpose=purpose,
                private=bool(analysis.get("privacy_sensitive")),
                high_risk=bool(analysis.get("high_risk")),
                temperature=0.1,
                max_tokens=int(self.config.get("evolution", "llm_max_tokens", default=1800)),
                timeout=int(self.config.get("evolution", "llm_timeout_seconds", default=20)),
            )
            parsed = parse_json_value(response.get("content", ""))
        except (RobustJSONError, ModelError, ModelUnavailable, KeyError, TypeError, ValueError):
            return []

        raw_items: list[Any]
        if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
            raw_items = parsed["proposals"]
        elif isinstance(parsed, list):
            raw_items = parsed
        else:
            return []

        proposals: list[dict[str, Any]] = []
        for item in raw_items[:3]:
            proposal = self._llm_item_to_proposal(trajectory, item)
            if proposal:
                proposals.append(proposal)
        return proposals

    def _llm_evolution_messages(self, trajectory: dict[str, Any]) -> list[dict[str, str]]:
        compact = {
            "task_id": trajectory.get("task_id"),
            "user_task": trajectory.get("user_task"),
            "task_analysis": trajectory.get("task_analysis"),
            "result": trajectory.get("result"),
            "reward_report": trajectory.get("reward_report"),
            "actions": [
                {
                    "step": action.get("step"),
                    "action_type": action.get("action_type"),
                    "status": action.get("status"),
                    "executor": action.get("executor"),
                    "output": str(action.get("observation", {}).get("output", ""))[:500],
                }
                for action in trajectory.get("actions", [])[:12]
            ],
            "executor_attribution": (
                (trajectory.get("reward_report", {}).get("objective_signals", {}) or {}).get("executor_attribution")
                if isinstance(trajectory.get("reward_report", {}).get("objective_signals", {}), dict)
                else None
            ),
        }
        system = (
            "You propose optional Praxile experience assets from one trajectory. "
            "Return JSON only: {\"proposals\":[...]}. Each proposal must include type, title, reason, risk_level, "
            "evidence, confidence, applicability_scope, anti_scope, and changes. "
            "Evidence must cite concrete trajectory signals. Do not propose secrets, hidden files, safety bypasses, "
            "architecture gates, frozen boundaries, or direct config mutations. All output is pending user approval."
        )
        user = (
            "Allowed types: memory_update, skill_create, eval_case, failure_pattern, harness_rule, routing.\n"
            "Allowed change roots: memory/, skills/, evals/checklists/, evals/regression-cases/, "
            "experience/failures/, rules/harness-rules/ except default.md.\n"
            f"Trajectory JSON:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _llm_item_to_proposal(self, trajectory: dict[str, Any], item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        evidence = item.get("evidence")
        if not evidence or not isinstance(evidence, list) or not all(isinstance(value, str) and value.strip() for value in evidence):
            return None
        changes = item.get("changes")
        if not isinstance(changes, list) or not changes:
            return None
        normalized_changes: list[dict[str, str]] = []
        for change in changes[:3]:
            if not isinstance(change, dict):
                return None
            operation = str(change.get("operation", "write"))
            path = str(change.get("path", ""))
            content = str(change.get("content", ""))
            if operation not in {"write", "append"} or not self._llm_change_path_allowed(path):
                return None
            normalized_changes.append({"operation": operation, "path": path, "content": content})
        proposal_type = str(item.get("type", "memory_update"))
        if proposal_type not in {
            "memory_update",
            "skill_create",
            "eval_case",
            "failure_pattern",
            "harness_rule",
            "routing",
        }:
            return None
        confidence = item.get("confidence", 0.5)
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            return None
        return self._proposal(
            source_task_id=trajectory["task_id"],
            proposal_type=proposal_type,
            title=str(item.get("title") or f"LLM-assisted {proposal_type}"),
            reason=str(item.get("reason") or "LLM-assisted experience proposal with cited evidence."),
            risk_level=str(item.get("risk_level") or "low"),
            evidence=evidence,
            confidence=confidence_value,
            affected_files=_edited_paths(trajectory),
            trigger_reason=str(item.get("trigger_reason") or item.get("reason") or "LLM-assisted proposal with cited evidence."),
            future_applicability=str(item.get("future_applicability") or item.get("applicability_scope") or "Only similar future tasks with matching evidence."),
            applicability_scope=str(item.get("applicability_scope") or "Only similar future tasks with matching evidence."),
            anti_scope=str(item.get("anti_scope") or "Do not apply outside the cited evidence and project scope."),
            generated_by="llm_assisted_evolution",
            changes=normalized_changes,
        )

    def _llm_change_path_allowed(self, path: str) -> bool:
        if not path or path != path.strip():
            return False
        unsafe_tokens = ("..", "\\", ":", "\x00")
        if any(token in path for token in unsafe_tokens):
            return False
        blocked = (
            "rules/frozen-boundaries/",
            "rules/architecture-gates/",
            "rules/harness-rules/default.md",
            "config.json",
        )
        if any(path.startswith(prefix) for prefix in blocked):
            return False
        allowed = (
            "memory/",
            "skills/",
            "evals/checklists/",
            "evals/regression-cases/",
            "experience/failures/",
            "experience/patterns/",
            "rules/harness-rules/",
        )
        return any(path.startswith(prefix) for prefix in allowed)


def _normalized_risk(proposal_type: str, risk_level: str) -> str:
    risk = str(risk_level or "low").lower()
    if risk not in {"low", "medium", "high"}:
        risk = "low"
    if proposal_type in {"architecture_gate", "frozen_boundary"}:
        return "high"
    return risk


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_level(confidence: float) -> str:
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        value = 0.0
    if value >= 0.75:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def _proposal_priority(proposal_type: str, risk_level: str, confidence: float) -> str:
    if proposal_type in {"architecture_gate", "frozen_boundary"}:
        return "p0"
    if risk_level == "high":
        return "p0"
    if proposal_type in {"failure_pattern", "skill_create", "harness_rule"} or float(confidence or 0) >= 0.75:
        return "p1"
    return "p2"


def _summarize_evidence(evidence: list[str]) -> str:
    if not evidence:
        return "No explicit evidence was recorded."
    first = evidence[0].strip()
    if len(evidence) == 1:
        return first
    return f"{first} (+{len(evidence) - 1} more signal(s))"


def _edited_paths(trajectory: dict[str, Any]) -> list[str]:
    return [
        str(action.get("input", {}).get("path"))
        for action in trajectory.get("actions", [])
        if action.get("action_type") == "edit_file"
        and action.get("status") == "success"
        and action.get("input", {}).get("path")
    ]


def _verification_commands(trajectory: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for item in trajectory.get("reward_report", {}).get("test_results", []) or []:
        command = item.get("data", {}).get("command")
        if command:
            commands.append(str(command))
    for action in trajectory.get("actions", []):
        if action.get("action_type") in {"run_test", "run_command"} and action.get("status") == "success":
            command = action.get("input", {}).get("command") or action.get("observation", {}).get("data", {}).get("command")
            if command:
                commands.append(str(command))
    return list(dict.fromkeys(commands))


def _failed_commands(trajectory: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for item in trajectory.get("reward_report", {}).get("test_results", []) or []:
        if item.get("status") != "success":
            command = item.get("data", {}).get("command")
            if command:
                commands.append(str(command))
    for action in trajectory.get("actions", []):
        if action.get("status") in {"failure", "blocked"}:
            command = action.get("input", {}).get("command") or action.get("observation", {}).get("data", {}).get("command")
            if command:
                commands.append(str(command))
    return list(dict.fromkeys(commands))


def _failure_excerpts(trajectory: dict[str, Any]) -> list[str]:
    excerpts: list[str] = []
    for item in trajectory.get("reward_report", {}).get("test_results", []) or []:
        if item.get("status") != "success" and item.get("output"):
            excerpts.append(str(item.get("output", "")).strip()[:300])
    for action in trajectory.get("actions", []):
        if action.get("status") in {"failure", "blocked"}:
            output = action.get("observation", {}).get("output")
            if output:
                excerpts.append(str(output).strip()[:300])
    return [excerpt for excerpt in dict.fromkeys(excerpts) if excerpt]


def _paths_from_actions(trajectory: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for action in trajectory.get("actions", []):
        for key in ["path", "target", "file"]:
            value = action.get("input", {}).get(key)
            if value:
                paths.append(str(value))
        data = action.get("observation", {}).get("data", {})
        if isinstance(data, dict):
            for key in ["path", "file", "artifact"]:
                value = data.get(key)
                if value:
                    paths.append(str(value))
    return list(dict.fromkeys(paths))


def _project_terms(trajectory: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for path in _edited_paths(trajectory) + _paths_from_actions(trajectory):
        clean = path.strip()
        if clean:
            terms.append(clean)
            parts = [part for part in Path(clean).parts if part not in {".", ".."}]
            terms.extend(part for part in parts[-3:] if part and "." not in part)
    for command in _failed_commands(trajectory) + _verification_commands(trajectory):
        terms.append(command)
        for part in command.split():
            if "/" in part or part.endswith((".py", ".js", ".ts", ".go", ".rs")):
                terms.append(part)
    task_words = [word for word in slugify(trajectory.get("user_task", ""), max_length=80).split("-") if len(word) > 2]
    terms.extend(task_words[:4])
    return list(dict.fromkeys(terms))[:12]


def _applies_when_lines(
    project_terms: list[str],
    edited_paths: list[str],
    failed_commands: list[str],
    verification_commands: list[str],
) -> list[str]:
    lines: list[str] = []
    for path in edited_paths[:4]:
        lines.append(f"- Future work touches `{path}` or the same module.")
    for command in (failed_commands or verification_commands)[:3]:
        lines.append(f"- Future work uses or repairs `{command}`.")
    for term in project_terms[:4]:
        if not any(term in line for line in lines):
            lines.append(f"- Future task context matches `{term}`.")
    return lines or ["- Future task explicitly matches the source run's task, files, commands, or failure signature."]


def _project_specific_title(prefix: str, trajectory: dict[str, Any]) -> str:
    terms = _project_terms(trajectory)
    context = next((term for term in terms if "/" in term or "." in term), None) or (terms[0] if terms else "")
    if context:
        return f"{prefix}: {context}"
    return prefix


def _skill_context_slug(trajectory: dict[str, Any], fallback: str) -> str:
    terms = _project_terms(trajectory)
    for term in terms:
        if "/" in term:
            return slugify(f"{fallback}-{Path(term).stem}", max_length=48)
    for term in terms:
        if term and term not in {fallback} and (" " in term or term.endswith((".py", ".js", ".ts", ".go", ".rs"))):
            return slugify(f"{fallback}-{term}", max_length=48)
    return fallback


def _project_context_lines(trajectory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    edited_paths = _edited_paths(trajectory)
    failed_commands = _failed_commands(trajectory)
    verification_commands = _verification_commands(trajectory)
    failure_excerpts = _failure_excerpts(trajectory)
    if edited_paths:
        lines.append(f"- Touched files: {', '.join(f'`{path}`' for path in edited_paths[:6])}")
    if failed_commands:
        lines.append(f"- Failure command(s): {', '.join(f'`{command}`' for command in failed_commands[:3])}")
    if verification_commands:
        lines.append(f"- Verification command(s): {', '.join(f'`{command}`' for command in verification_commands[:3])}")
    if failure_excerpts:
        lines.append(f"- Failure signature: `{_failure_signature(failure_excerpts[0])}`")
    return lines or ["- Source trajectory did not record a concrete file or command; use only with close task similarity."]


def _skill_source_evidence_lines(trajectory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    edited_paths = _edited_paths(trajectory)
    failed_commands = _failed_commands(trajectory)
    verification_commands = _verification_commands(trajectory)
    for path in edited_paths[:4]:
        lines.append(f"- Concrete file from trajectory: `{path}`")
    for command in failed_commands[:3]:
        lines.append(f"- Failing command from trajectory: `{command}`")
    for command in verification_commands[:3]:
        lines.append(f"- Verification command from trajectory: `{command}`")
    for action in _fix_action_lines(trajectory)[:4]:
        lines.append(f"- Observed repair action: {action}")
    lines.extend(_executor_evidence_lines(trajectory)[:3])
    return lines or ["- No concrete file or command was recorded; keep this skill as a draft until a stronger run confirms it."]


def _executor_evidence_lines(trajectory: dict[str, Any]) -> list[str]:
    report = trajectory.get("reward_report", {}) if isinstance(trajectory.get("reward_report"), dict) else {}
    objective = report.get("objective_signals", {}) if isinstance(report.get("objective_signals"), dict) else {}
    attribution = objective.get("executor_attribution") if isinstance(objective.get("executor_attribution"), dict) else {}
    if not attribution:
        executors = trajectory.get("executors") or []
        if not executors:
            return []
        attribution = {
            "quality": "recorded",
            "executors": executors,
            "action_executor_counts": _action_executor_counts(trajectory),
            "parallel_readonly": trajectory.get("parallel_readonly_exploration") or {},
        }

    lines: list[str] = []
    quality = attribution.get("quality")
    if quality:
        lines.append(f"- Executor attribution quality: `{quality}`")
    counts = attribution.get("action_executor_counts") if isinstance(attribution.get("action_executor_counts"), dict) else {}
    if counts:
        rendered = ", ".join(f"`{key}`={value}" for key, value in list(counts.items())[:6])
        lines.append(f"- Top-level action ownership: {rendered}")
    parallel = attribution.get("parallel_readonly") if isinstance(attribution.get("parallel_readonly"), dict) else {}
    if parallel.get("enabled"):
        lines.append(
            "- Parallel read-only exploration: "
            f"{parallel.get('subaction_count', parallel.get('action_count', 0))} subaction(s), "
            f"{parallel.get('worker_count', 0)} worker(s), "
            f"{parallel.get('failed_observation_count', 0)} failed, "
            f"{parallel.get('blocked_observation_count', 0)} blocked."
        )
    executors = attribution.get("executors") if isinstance(attribution.get("executors"), list) else []
    worker_roles = [
        f"`{item.get('executor_id')}`:{item.get('role') or item.get('kind')}"
        for item in executors
        if isinstance(item, dict) and str(item.get("kind") or "").startswith("readonly")
    ]
    if worker_roles:
        lines.append(f"- Read-only workers: {', '.join(worker_roles[:6])}")
    return lines


def _action_executor_counts(trajectory: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in trajectory.get("actions", []) or []:
        executor = action.get("executor") if isinstance(action.get("executor"), dict) else {}
        executor_id = str(executor.get("executor_id") or "").strip()
        if executor_id:
            counts[executor_id] = counts.get(executor_id, 0) + 1
    return counts


def _fix_action_lines(trajectory: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for action in trajectory.get("actions", []):
        action_type = action.get("action_type")
        status = action.get("status")
        if action_type == "edit_file" and status == "success":
            path = action.get("input", {}).get("path")
            if path:
                lines.append(f"Changed `{path}` during the attempted repair.")
        elif action_type in {"run_command", "run_test"} and status == "success":
            command = action.get("input", {}).get("command") or action.get("observation", {}).get("data", {}).get("command")
            if command:
                lines.append(f"Verified with `{command}` after the repair attempt.")
        elif status == "blocked":
            command = action.get("input", {}).get("command")
            if command:
                lines.append(f"Stopped instead of bypassing blocked command `{command}`.")
    return list(dict.fromkeys(lines)) or ["No successful fix action was recorded; treat this as a failure guardrail, not a proven repair recipe."]


def _failure_signature(text: str) -> str:
    stripped = " ".join(str(text or "").split())
    if not stripped:
        return "unknown"
    for marker in ["AssertionError", "ImportError", "ModuleNotFoundError", "PermissionError", "TimeoutError", "ValueError"]:
        if marker in stripped:
            return marker
    return stripped[:120]


def _recent_rejected_proposals(directory: Path, *, limit: int) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _matching_rejections(proposal: dict[str, Any], rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    proposal_type = str(proposal.get("type") or "")
    title_key = slugify(str(proposal.get("title") or ""), max_length=80)
    proposal_terms = set(_proposal_terms_for_feedback(proposal))
    matches: list[dict[str, Any]] = []
    for item in rejected:
        if str(item.get("type") or item.get("feedback", {}).get("proposal_type") or "") != proposal_type:
            continue
        feedback = item.get("feedback") if isinstance(item.get("feedback"), dict) else {}
        rejected_title = slugify(str(item.get("title") or feedback.get("proposal_title") or ""), max_length=80)
        rejected_terms = set(feedback.get("trigger_terms") or _proposal_terms_for_feedback(item))
        if title_key and rejected_title and title_key == rejected_title:
            matches.append(item)
            continue
        if len(proposal_terms.intersection(rejected_terms)) >= 3:
            matches.append(item)
    return matches


def _proposal_terms_for_feedback(proposal: dict[str, Any]) -> list[str]:
    values: list[str] = [
        str(proposal.get("title") or ""),
        str(proposal.get("trigger_reason") or ""),
        str(proposal.get("future_applicability") or ""),
        str(proposal.get("applicability_scope") or ""),
    ]
    values.extend(str(value) for value in proposal.get("target_files") or [])
    values.extend(str(value) for value in proposal.get("affected_files") or [])
    values.extend(str(value) for value in proposal.get("evidence") or [])
    for change in proposal.get("changes") or []:
        if isinstance(change, dict):
            values.append(str(change.get("path") or ""))
            values.append(str(change.get("content") or ""))
    terms: list[str] = []
    for value in values:
        for token in re.findall(r"[A-Za-z0-9_\-/\.]+|[\u4e00-\u9fff]+", value.lower()):
            token = token.strip("`.,:;()[]{}")
            if len(token) > 2 and token not in {"memory", "skill", "proposal", "update", "experience"}:
                terms.append(token)
    return list(dict.fromkeys(terms))[:32]
