from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import Config
from ..evolution import EvolutionEngine
from ..store import ExperienceStore
from ..utils import new_id, shorten, slugify, stable_hash, utc_now, write_json


P0_MODES = {"duplicates", "stale", "silent_failures"}
P1_MODES = {"harmful", "rejected_proposals", "high_value_patterns"}
ALL_MODES = P0_MODES | P1_MODES


@dataclass(frozen=True)
class ReflectScope:
    since: str | None = None
    asset: str | None = None
    modes: frozenset[str] = frozenset()
    stale_days: int | None = None

    def enabled_modes(self) -> set[str]:
        return set(self.modes or P0_MODES)


class ReflectEngine:
    """Offline, proposal-governed experience refinement."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.evolution = EvolutionEngine(config)

    def run(self, scope: ReflectScope, *, write_proposals: bool = False) -> dict[str, Any]:
        self.store.reindex_all()
        reflect_id = new_id("reflect")
        cutoff = _parse_since(scope.since)
        context = self._context(cutoff=cutoff)
        selected_asset = _normalize_asset_path(scope.asset) if scope.asset else None
        findings: list[dict[str, Any]] = []
        modes = scope.enabled_modes()
        if "duplicates" in modes:
            findings.extend(self._duplicate_findings(context, reflect_id=reflect_id, selected_asset=selected_asset))
        if "stale" in modes:
            findings.extend(self._stale_findings(context, reflect_id=reflect_id, selected_asset=selected_asset, days=scope.stale_days))
        if "silent_failures" in modes and not selected_asset:
            findings.extend(self._silent_failure_findings(context, reflect_id=reflect_id))
        if "harmful" in modes:
            findings.extend(self._harmful_findings(context, reflect_id=reflect_id, selected_asset=selected_asset))
        if "rejected_proposals" in modes and not selected_asset:
            findings.extend(self._rejected_proposal_findings(context, reflect_id=reflect_id))
        if "high_value_patterns" in modes:
            findings.extend(self._high_value_pattern_findings(context, reflect_id=reflect_id, selected_asset=selected_asset))

        max_findings = int(self.config.get("reflect", "max_findings", default=50) or 50)
        findings = sorted(findings, key=lambda item: (-float(item.get("confidence") or 0), item.get("type", ""), item.get("finding_id", "")))[:max_findings]
        candidates = [item["proposal_candidate"] for item in findings if isinstance(item.get("proposal_candidate"), dict)]
        gated, suppressed = self._gate_proposals(candidates, reflect_id)
        written_paths: list[str] = []
        if write_proposals:
            for proposal in gated:
                path = self.store.write_proposal(proposal)
                written_paths.append(str(path.relative_to(self.config.paths.root)))

        report = {
            "schema_version": 1,
            "reflect_id": reflect_id,
            "created_at": utc_now(),
            "scope": {
                "since": scope.since,
                "asset": selected_asset,
                "modes": sorted(modes),
                "stale_days": scope.stale_days or self._stale_days(),
            },
            "inputs": {
                "runs": [_compact_run(item) for item in context["runs"]],
                "asset_count": len(context["assets"]),
                "proposal_count": len(context["proposals"]),
                "feedback_count": len(context["feedback"]),
                "silent_failure_signal_count": len(context["silent_failure_signals"]),
                "graph_snapshot": self.store.graph_status(),
            },
            "asset_summary": _selected_asset_summary(context, selected_asset) if selected_asset else None,
            "findings": findings,
            "generated_proposals": [_compact_proposal(item) for item in gated],
            "suppressed_proposals": [_compact_proposal(item) for item in suppressed],
            "written_proposal_paths": written_paths,
            "summary": _summary(findings, gated, context),
            "no_assets_modified": True,
        }
        if write_proposals:
            report["report_path"] = self.write_report(report)
        return report

    def write_report(self, report: dict[str, Any], output: Path | None = None) -> str:
        path = output or (self.config.paths.state / "experience" / "reflect" / f"{report['reflect_id']}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, report)
        return str(path.relative_to(self.config.paths.root))

    def _context(self, *, cutoff: datetime | None) -> dict[str, Any]:
        rows = self.store.list_history(limit=10000)
        runs: list[dict[str, Any]] = []
        for row in rows:
            timestamp = _parse_timestamp(row.get("updated_at") or row.get("created_at"))
            if cutoff and timestamp and timestamp < cutoff:
                continue
            task_id = str(row.get("task_id") or "")
            trajectory = self.store.get_trajectory(task_id) if task_id else None
            if trajectory:
                runs.append(trajectory)
        assets: list[dict[str, Any]] = []
        for kind in ["memory", "failure", "eval", "skill", "rule"]:
            assets.extend(self.store.list_assets(kind))
        proposals = [
            item
            for item in self.store.list_proposals(status=None, limit=10000)
            if not cutoff or _after_cutoff(item.get("updated_at") or item.get("created_at"), cutoff)
        ]
        feedback = [
            item
            for item in self.store.list_feedback()
            if not cutoff or _after_cutoff(item.get("created_at"), cutoff)
        ]
        silent_signals: list[dict[str, Any]] = []
        for run in runs:
            for signal in run.get("silent_failure_signals") or []:
                if isinstance(signal, dict):
                    silent_signals.append({"task_id": run.get("task_id"), **signal})
        return {
            "runs": runs,
            "assets": assets,
            "proposals": proposals,
            "feedback": feedback,
            "silent_failure_signals": silent_signals,
        }

    def _duplicate_findings(self, context: dict[str, Any], *, reflect_id: str, selected_asset: str | None) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for asset in _active_assets(context["assets"]):
            if selected_asset and asset.get("path") != selected_asset:
                continue
            key = _duplicate_key(asset)
            if key:
                grouped.setdefault(key, []).append(asset)
        if selected_asset:
            for asset in _active_assets(context["assets"]):
                key = _duplicate_key(asset)
                if key and key in grouped and asset not in grouped[key]:
                    grouped[key].append(asset)
        min_count = int(self.config.get("reflect", "duplicate_min_assets", default=2) or 2)
        findings: list[dict[str, Any]] = []
        for key, items in grouped.items():
            if len(items) < min_count:
                continue
            finding_id = _finding_id("duplicate", key)
            finding = {
                "finding_id": finding_id,
                "type": "duplicate_asset",
                "severity": "medium",
                "confidence": 0.78,
                "evidence_refs": [str(item.get("path")) for item in items],
                "affected_assets": [str(item.get("path")) for item in items],
                "affected_runs": sorted({str(item.get("source_task_id")) for item in items if item.get("source_task_id")}),
                "affected_proposals": [],
                "reason": f"{len(items)} active assets share normalized topic `{key}` and may pollute retrieval with overlapping guidance.",
                "recommended_action": "merge",
            }
            finding["proposal_candidate"] = self._asset_merge_proposal(reflect_id, finding, items, key=key)
            findings.append(finding)
        return findings

    def _stale_findings(
        self,
        context: dict[str, Any],
        *,
        reflect_id: str,
        selected_asset: str | None,
        days: int | None,
    ) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days or self._stale_days()))
        findings: list[dict[str, Any]] = []
        for asset in _active_assets(context["assets"]):
            path = str(asset.get("path") or "")
            if selected_asset and path != selected_asset:
                continue
            if int(asset.get("usage_count") or 0) > 0:
                continue
            # Reindexing refreshes updated_at, so stale governance must use the
            # original asset age rather than the latest index bookkeeping time.
            timestamp = _parse_timestamp(asset.get("created_at") or asset.get("updated_at"))
            if timestamp and timestamp > cutoff:
                continue
            if asset.get("type") in {"architecture_gate", "frozen_boundary", "harness_rule"}:
                risk = "medium"
                action = "inspect"
                confidence = 0.58
            else:
                risk = "low"
                action = "deprecate"
                confidence = 0.72
            finding = {
                "finding_id": _finding_id("stale", path),
                "type": "stale_asset",
                "severity": risk,
                "confidence": confidence,
                "evidence_refs": [path],
                "affected_assets": [path],
                "affected_runs": [str(asset.get("source_task_id"))] if asset.get("source_task_id") else [],
                "affected_proposals": [],
                "reason": f"`{path}` is active, unused, and older than the stale threshold.",
                "recommended_action": action,
            }
            if action == "deprecate":
                finding["proposal_candidate"] = self._asset_deprecate_proposal(reflect_id, finding, asset, reason="reflect stale unused asset scan")
            findings.append(finding)
        return findings

    def _silent_failure_findings(self, context: dict[str, Any], *, reflect_id: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for signal in context["silent_failure_signals"]:
            signal_type = str(signal.get("type") or signal.get("signal") or "unknown")
            grouped.setdefault(signal_type, []).append(signal)
        min_count = int(self.config.get("reflect", "silent_failure_min_count", default=2) or 2)
        findings: list[dict[str, Any]] = []
        for signal_type, items in grouped.items():
            if len(items) < min_count:
                continue
            affected_runs = sorted({str(item.get("task_id")) for item in items if item.get("task_id")})
            finding = {
                "finding_id": _finding_id("silent", signal_type),
                "type": "silent_failure_pattern",
                "severity": "high" if len(items) >= min_count + 2 else "medium",
                "confidence": min(0.9, 0.62 + len(items) * 0.05),
                "evidence_refs": [f"run:{task_id}" for task_id in affected_runs],
                "affected_assets": [],
                "affected_runs": affected_runs,
                "affected_proposals": [],
                "reason": f"Silent failure signal `{signal_type}` appeared {len(items)} time(s) in scope.",
                "recommended_action": "create_harness_rule",
            }
            finding["proposal_candidate"] = self._silent_failure_rule_proposal(reflect_id, finding, signal_type, items)
            findings.append(finding)
        return findings

    def _harmful_findings(self, context: dict[str, Any], *, reflect_id: str, selected_asset: str | None) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        for asset in _active_assets(context["assets"]):
            path = str(asset.get("path") or "")
            if selected_asset and path != selected_asset:
                continue
            positive = int(asset.get("positive_outcome_count") or 0)
            negative = int(asset.get("negative_outcome_count") or 0)
            harmful_feedback = [
                item for item in context["feedback"]
                if item.get("target_type") == "asset" and _normalize_asset_path(item.get("target_id")) == path and item.get("sentiment") == "negative"
            ]
            if negative < 2 and not harmful_feedback:
                continue
            if negative <= positive and len(harmful_feedback) == 0:
                continue
            finding = {
                "finding_id": _finding_id("harmful", path),
                "type": "harmful_asset",
                "severity": "high" if negative > positive else "medium",
                "confidence": 0.76 if negative > positive else 0.64,
                "evidence_refs": [path, *[str(item.get("feedback_id")) for item in harmful_feedback[:4]]],
                "affected_assets": [path],
                "affected_runs": [],
                "affected_proposals": [],
                "reason": f"`{path}` has negative_outcome_count={negative}, positive_outcome_count={positive}, harmful_feedback={len(harmful_feedback)}.",
                "recommended_action": "deprecate",
            }
            finding["proposal_candidate"] = self._asset_deprecate_proposal(reflect_id, finding, asset, reason="reflect harmful asset scan")
            findings.append(finding)
        return findings

    def _rejected_proposal_findings(self, context: dict[str, Any], *, reflect_id: str) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for proposal in context["proposals"]:
            if proposal.get("status") != "rejected":
                continue
            key = _rejection_key(proposal)
            grouped.setdefault(key, []).append(proposal)
        min_count = int(self.config.get("reflect", "rejected_theme_min_count", default=2) or 2)
        findings: list[dict[str, Any]] = []
        for key, proposals in grouped.items():
            if len(proposals) < min_count:
                continue
            proposal_ids = [str(item.get("proposal_id")) for item in proposals if item.get("proposal_id")]
            finding = {
                "finding_id": _finding_id("rejected", key),
                "type": "rejected_proposal_theme",
                "severity": "medium",
                "confidence": min(0.85, 0.58 + len(proposals) * 0.06),
                "evidence_refs": [f"proposal:{item}" for item in proposal_ids],
                "affected_assets": [],
                "affected_runs": sorted({str(item.get("source_task_id")) for item in proposals if item.get("source_task_id")}),
                "affected_proposals": proposal_ids,
                "reason": f"{len(proposals)} rejected proposal(s) share theme `{key}`.",
                "recommended_action": "tighten_proposal_gate",
            }
            finding["proposal_candidate"] = self._proposal_gate_policy_proposal(reflect_id, finding, key, proposals)
            findings.append(finding)
        return findings

    def _high_value_pattern_findings(self, context: dict[str, Any], *, reflect_id: str, selected_asset: str | None) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        min_positive = int(self.config.get("reflect", "high_value_positive_min", default=3) or 3)
        for asset in _active_assets(context["assets"]):
            path = str(asset.get("path") or "")
            if selected_asset and path != selected_asset:
                continue
            if asset.get("type") not in {"project_pattern", "failure_pattern", "eval_checklist"}:
                continue
            positive = int(asset.get("positive_outcome_count") or 0)
            negative = int(asset.get("negative_outcome_count") or 0)
            if positive < min_positive or positive <= negative:
                continue
            finding = {
                "finding_id": _finding_id("promote", path),
                "type": "high_value_pattern",
                "severity": "medium",
                "confidence": min(0.9, 0.65 + positive * 0.04 - negative * 0.03),
                "evidence_refs": [path],
                "affected_assets": [path],
                "affected_runs": [],
                "affected_proposals": [],
                "reason": f"`{path}` has strong positive usage outcomes and may deserve a first-class skill or rule.",
                "recommended_action": "promote",
            }
            finding["proposal_candidate"] = self._pattern_promote_proposal(reflect_id, finding, asset)
            findings.append(finding)
        return findings

    def _gate_proposals(self, proposals: list[dict[str, Any]], reflect_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        gated: list[dict[str, Any]] = []
        suppressed: list[dict[str, Any]] = []
        synthetic = {
            "task_id": reflect_id,
            "reward_report": {"experience_generation": {"evidence_strength": "medium"}},
            "spec_context": {},
        }
        seen: set[str] = set()
        for proposal in proposals:
            fingerprint = stable_hash(json.dumps(proposal.get("changes") or [], sort_keys=True, ensure_ascii=False), length=16)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            gate = self.evolution._proposal_gate_decision(proposal, synthetic)
            proposal["proposal_gate"] = gate
            if gate.get("passed"):
                gated.append(proposal)
            else:
                suppressed.append(proposal)
        return gated, suppressed

    def _asset_merge_proposal(self, reflect_id: str, finding: dict[str, Any], items: list[dict[str, Any]], *, key: str) -> dict[str, Any]:
        canonical = sorted(items, key=lambda item: (-int(item.get("positive_outcome_count") or 0), -int(item.get("usage_count") or 0), item.get("path", "")))[0]
        canonical_path = str(canonical.get("path") or "")
        changes: list[dict[str, Any]] = []
        evidence = [
            finding["reason"],
            f"Canonical candidate: `{canonical_path}`.",
        ]
        for item in items:
            path = str(item.get("path") or "")
            if not path or path == canonical_path:
                continue
            changes.append(
                {
                    "path": path.removeprefix(".praxile/"),
                    "operation": "metadata_update",
                    "metadata": {
                        "status": "superseded",
                        "replaced_by": canonical_path,
                        "superseded_reason": f"Reflect duplicate group `{key}` selected `{canonical_path}` as canonical.",
                        "superseded_at": utc_now(),
                    },
                }
            )
            evidence.append(f"`{path}` overlaps with canonical `{canonical_path}`.")
        changes.append(
            {
                "path": canonical_path.removeprefix(".praxile/"),
                "operation": "append",
                "content": _merge_note(reflect_id, finding, items, canonical_path),
            }
        )
        return self._proposal(
            reflect_id,
            finding,
            proposal_type="asset_merge",
            title=f"Merge duplicate experience assets for `{key}`",
            reason="Reflect found overlapping durable experience that may add retrieval noise.",
            risk_level="low",
            evidence=evidence,
            confidence=0.76,
            future_applicability="Experience governance only; accepting supersedes duplicates and appends reviewed source evidence to the canonical asset.",
            applicability_scope=f"Only the duplicate group `{key}` in this repository-local .praxile state.",
            anti_scope="Do not delete source assets; do not apply if the assets encode intentionally different architecture or safety guidance.",
            changes=changes,
        )

    def _asset_deprecate_proposal(self, reflect_id: str, finding: dict[str, Any], asset: dict[str, Any], *, reason: str) -> dict[str, Any]:
        path = str(asset.get("path") or "")
        return self._proposal(
            reflect_id,
            finding,
            proposal_type="asset_deprecate",
            title=f"Deprecate experience asset `{path.removeprefix('.praxile/')}`",
            reason=finding["reason"],
            risk_level=str(finding.get("severity") or "low"),
            evidence=[
                finding["reason"],
                f"Asset `{path}` usage={asset.get('usage_count')} positive={asset.get('positive_outcome_count')} negative={asset.get('negative_outcome_count')}.",
            ],
            confidence=float(finding.get("confidence") or 0.7),
            future_applicability="Experience governance only; accepting marks this one asset deprecated so normal retrieval stops loading it.",
            applicability_scope="The specified repository-local experience asset only.",
            anti_scope="Do not delete the asset file; deprecation remains auditable and can be rolled back.",
            changes=[
                {
                    "path": path.removeprefix(".praxile/"),
                    "operation": "metadata_update",
                    "metadata": {
                        "status": "deprecated",
                        "deprecated_reason": reason,
                        "deprecated_at": utc_now(),
                        "reflect_id": reflect_id,
                        "reflect_finding_id": finding.get("finding_id"),
                    },
                }
            ],
        )

    def _silent_failure_rule_proposal(self, reflect_id: str, finding: dict[str, Any], signal_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        slug = slugify(f"reflect-{signal_type}", max_length=56)
        affected_runs = finding.get("affected_runs") or []
        content = (
            f"# Harness Rule: {slug}\n\n"
            "## Rule\n"
            f"When silent failure signal `{signal_type}` repeats in recent runs, require explicit verification or review before turning the run into durable experience.\n\n"
            "## Evidence\n"
            + "\n".join(f"- `{task_id}`" for task_id in affected_runs[:12])
            + "\n\n## Applies When\n"
            f"- A future task produces `{signal_type}` or a close variant.\n"
            "- A task appears completed but lacks enough objective verification or scope control.\n\n"
            "## Does Not Apply When\n"
            "- The user explicitly marks the run as exploratory and no durable experience proposal is being generated.\n\n"
            "## Rollback\n"
            "- Reject or roll back this proposal to remove the harness rule from active retrieval.\n"
        )
        return self._proposal(
            reflect_id,
            finding,
            proposal_type="harness_rule_create",
            title=f"Add harness rule for repeated `{signal_type}`",
            reason=finding["reason"],
            risk_level="medium",
            evidence=[
                finding["reason"],
                f"Affected runs: {', '.join(affected_runs[:8]) or 'not recorded'}.",
                f"Signal examples: {len(items)}.",
            ],
            confidence=float(finding.get("confidence") or 0.72),
            future_applicability=f"Future runs that emit `{signal_type}` should require stronger verification or human review before durable experience updates.",
            applicability_scope=f"Silent failure signal `{signal_type}` and close variants in this repository.",
            anti_scope="Do not block ordinary low-risk tasks that have explicit verification and no durable proposal output.",
            changes=[{"path": f"rules/harness-rules/{slug}.md", "operation": "write", "content": content}],
        )

    def _proposal_gate_policy_proposal(self, reflect_id: str, finding: dict[str, Any], key: str, proposals: list[dict[str, Any]]) -> dict[str, Any]:
        slug = slugify(f"proposal-gate-{key}", max_length=56)
        ids = [str(item.get("proposal_id")) for item in proposals if item.get("proposal_id")]
        content = (
            f"# Harness Rule: {slug}\n\n"
            "## Rule\n"
            f"If proposal theme `{key}` repeats as rejected, require stronger concrete evidence, anti-scope, and reviewer inspection before similar proposals are accepted.\n\n"
            "## Evidence\n"
            + "\n".join(f"- `{proposal_id}`" for proposal_id in ids[:12])
            + "\n\n## Applies When\n"
            f"- A new proposal matches rejected theme `{key}`.\n"
            "- The proposal is low-confidence, broad, or missing concrete project evidence.\n\n"
            "## Does Not Apply When\n"
            "- A human explicitly asks to preserve the proposal despite the theme.\n\n"
            "## Rollback\n"
            "- Reject or roll back this proposal to remove this proposal-gate refinement.\n"
        )
        return self._proposal(
            reflect_id,
            finding,
            proposal_type="proposal_gate_policy_update",
            title=f"Tighten proposal gate for rejected theme `{key}`",
            reason=finding["reason"],
            risk_level="medium",
            evidence=[finding["reason"], f"Rejected proposal IDs: {', '.join(ids[:8])}."],
            confidence=float(finding.get("confidence") or 0.68),
            future_applicability=f"Future proposals matching `{key}` should be inspected or edited before acceptance.",
            applicability_scope=f"Proposal theme `{key}` in this repository.",
            anti_scope="Do not reject well-evidenced proposals only because they share a broad type.",
            changes=[{"path": f"rules/harness-rules/{slug}.md", "operation": "write", "content": content}],
        )

    def _pattern_promote_proposal(self, reflect_id: str, finding: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
        slug = slugify(str(asset.get("title") or Path(str(asset.get("path") or "pattern")).stem), max_length=48)
        content = (
            f"# Skill: {slug}\n\n"
            "## Purpose\n"
            f"Use this skill when a future task matches the high-value pattern from `{asset.get('path')}`.\n\n"
            "## Evidence\n"
            f"- Source asset: `{asset.get('path')}`\n"
            f"- Positive outcomes: {asset.get('positive_outcome_count', 0)}\n"
            f"- Negative outcomes: {asset.get('negative_outcome_count', 0)}\n\n"
            "## Workflow\n"
            "- Read the source pattern before editing.\n"
            "- Confirm the task matches the same files, failure signature, command, or project area.\n"
            "- Run the verification command recorded in the source pattern when available.\n"
            "- Treat mismatches as counterexamples rather than forcing the pattern.\n\n"
            "## Source Pattern Excerpt\n"
            f"{shorten(str(asset.get('summary') or ''), 1200)}\n"
        )
        return self._proposal(
            reflect_id,
            finding,
            proposal_type="pattern_promote",
            title=f"Promote high-value pattern `{slug}` to a skill",
            reason=finding["reason"],
            risk_level="medium",
            evidence=[finding["reason"], f"Source asset `{asset.get('path')}`."],
            confidence=float(finding.get("confidence") or 0.72),
            future_applicability="Future tasks matching this high-value pattern can load a more explicit project skill.",
            applicability_scope="Only tasks matching the source pattern's files, commands, failure signatures, and project area.",
            anti_scope="Do not apply when the source pattern has known counterexamples or the task touches unrelated architecture/security/UX constraints.",
            changes=[{"path": f"skills/{slug}/SKILL.md", "operation": "write", "content": content}],
        )

    def _proposal(
        self,
        reflect_id: str,
        finding: dict[str, Any],
        *,
        proposal_type: str,
        title: str,
        reason: str,
        risk_level: str,
        evidence: list[str],
        confidence: float,
        future_applicability: str,
        applicability_scope: str,
        anti_scope: str,
        changes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        proposal = self.evolution._proposal(
            source_task_id=reflect_id,
            proposal_type=proposal_type,
            title=title,
            reason=reason,
            risk_level=risk_level,
            evidence=evidence,
            confidence=confidence,
            trigger_reason="Reflective experience governance scan.",
            future_applicability=future_applicability,
            applicability_scope=applicability_scope,
            anti_scope=anti_scope,
            changes=changes,
            generated_by="reflect",
        )
        proposal["source"] = {
            "type": "reflect",
            "reflect_id": reflect_id,
            "finding_id": finding.get("finding_id"),
        }
        proposal["reflect_id"] = reflect_id
        proposal["finding_id"] = finding.get("finding_id")
        proposal["reflect_reason"] = finding.get("reason")
        proposal["affected_assets"] = finding.get("affected_assets") or []
        proposal["affected_runs"] = finding.get("affected_runs") or []
        proposal["affected_proposals"] = finding.get("affected_proposals") or []
        return proposal

    def _stale_days(self) -> int:
        return int(
            self.config.get(
                "reflect",
                "stale_days",
                default=self.config.get("evolution", "consolidation_stale_days", default=90),
            )
            or 90
        )


def format_reflect_summary(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    scope = report.get("scope") if isinstance(report.get("scope"), dict) else {}
    lines = [
        "Praxile Reflect Report",
        f"Reflect ID: {report.get('reflect_id')}",
        f"Scope: since={scope.get('since') or 'all'} asset={scope.get('asset') or 'all'} modes={', '.join(scope.get('modes') or [])}",
        "",
        "Analyzed:",
        f"- Runs: {summary.get('runs', 0)}",
        f"- Assets: {summary.get('assets', 0)}",
        f"- Proposals: {summary.get('proposals', 0)}",
        f"- Feedback events: {summary.get('feedback', 0)}",
        f"- Silent failure signals: {summary.get('silent_failure_signals', 0)}",
        "",
        "Findings:",
    ]
    for key, value in (summary.get("finding_counts") or {}).items():
        lines.append(f"- {key}: {value}")
    if not summary.get("finding_counts"):
        lines.append("- none")
    lines.extend(["", "Recommended proposals:"])
    for key, value in (summary.get("proposal_counts") or {}).items():
        lines.append(f"- {key}: {value}")
    if not summary.get("proposal_counts"):
        lines.append("- none")
    asset = report.get("asset_summary") if isinstance(report.get("asset_summary"), dict) else {}
    if asset:
        lines.extend(["", "Asset:", f"- Path: {asset.get('path')}", f"- Found: {asset.get('found')}"])
        if asset.get("found"):
            lines.extend(
                [
                    f"- Status: {asset.get('status')}",
                    f"- Usage count: {asset.get('usage_count', 0)}",
                    f"- Positive outcomes: {asset.get('positive_outcome_count', 0)}",
                    f"- Negative outcomes: {asset.get('negative_outcome_count', 0)}",
                    f"- Last used: {asset.get('last_used_at') or 'never'}",
                ]
            )
    if report.get("written_proposal_paths"):
        lines.extend(["", "Written proposals:"])
        for path in report["written_proposal_paths"]:
            lines.append(f"- {path}")
    else:
        lines.extend(["", "No proposals were written. Run with --write-proposals to create reviewable proposals."])
    lines.append("No active assets were modified.")
    ci = report.get("ci") if isinstance(report.get("ci"), dict) else {}
    if ci:
        lines.extend(
            [
                "",
                "CI:",
                f"- Passed: {ci.get('passed')}",
                f"- Exit code: {ci.get('exit_code')}",
            ]
        )
        for failure in ci.get("failures") or []:
            if isinstance(failure, dict):
                lines.append(f"- failure[{failure.get('code')}]: {failure.get('message')}")
    artifacts = report.get("ci_artifacts") if isinstance(report.get("ci_artifacts"), dict) else {}
    if artifacts:
        lines.extend(["", "CI artifacts:"])
        for key in ["json", "markdown", "latest_json", "latest_markdown"]:
            if artifacts.get(key):
                lines.append(f"- {key}: {artifacts[key]}")
    if report.get("report_path"):
        lines.append(f"Reflect report: {report['report_path']}")
    return "\n".join(lines)


def format_reflect_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        f"# Praxile Reflect Report `{report.get('reflect_id')}`",
        "",
        "## Scope",
        "",
        "```json",
        json.dumps(report.get("scope") or {}, indent=2, ensure_ascii=False, sort_keys=True),
        "```",
        "",
        "## Summary",
        "",
        f"- Runs: {summary.get('runs', 0)}",
        f"- Assets: {summary.get('assets', 0)}",
        f"- Proposals: {summary.get('proposals', 0)}",
        f"- Feedback events: {summary.get('feedback', 0)}",
        f"- Silent failure signals: {summary.get('silent_failure_signals', 0)}",
        "",
    ]
    asset = report.get("asset_summary") if isinstance(report.get("asset_summary"), dict) else {}
    if asset:
        lines.extend(["## Asset", "", f"- Path: {asset.get('path')}", f"- Found: {asset.get('found')}"])
        if asset.get("found"):
            lines.extend(
                [
                    f"- Type: {asset.get('type')}",
                    f"- Status: {asset.get('status')}",
                    f"- Usage count: {asset.get('usage_count', 0)}",
                    f"- Positive outcomes: {asset.get('positive_outcome_count', 0)}",
                    f"- Negative outcomes: {asset.get('negative_outcome_count', 0)}",
                    f"- Last used: {asset.get('last_used_at') or 'never'}",
                    f"- Source task: {asset.get('source_task_id') or 'unknown'}",
                ]
            )
        lines.append("")
    lines.extend(["## Findings", ""])
    findings = report.get("findings") or []
    if not findings:
        lines.append("- None.")
    for finding in findings:
        lines.extend(
            [
                f"### {finding.get('type')} `{finding.get('finding_id')}`",
                "",
                f"- Severity: {finding.get('severity')}",
                f"- Confidence: {finding.get('confidence')}",
                f"- Recommended action: {finding.get('recommended_action')}",
                f"- Reason: {finding.get('reason')}",
                f"- Affected assets: {', '.join(finding.get('affected_assets') or []) or 'none'}",
                f"- Affected runs: {', '.join(finding.get('affected_runs') or []) or 'none'}",
                "",
            ]
        )
    lines.extend(["## Generated Proposals", ""])
    proposals = report.get("generated_proposals") or []
    if not proposals:
        lines.append("- None.")
    for proposal in proposals:
        lines.append(f"- `{proposal.get('proposal_id')}` [{proposal.get('type')}] {proposal.get('title')}")
    ci = report.get("ci") if isinstance(report.get("ci"), dict) else {}
    if ci:
        lines.extend(["", "## CI Check", ""])
        lines.append(f"- Passed: {ci.get('passed')}")
        lines.append(f"- Exit code: {ci.get('exit_code')}")
        for failure in ci.get("failures") or []:
            if isinstance(failure, dict):
                lines.append(f"- Failure `{failure.get('code')}`: {failure.get('message')}")
    artifacts = report.get("ci_artifacts") if isinstance(report.get("ci_artifacts"), dict) else {}
    if artifacts:
        lines.extend(["", "## CI Artifacts", ""])
        for key in ["json", "markdown", "latest_json", "latest_markdown"]:
            if artifacts.get(key):
                lines.append(f"- `{key}`: `{artifacts[key]}`")
    lines.extend(["", "Reflect does not directly modify active assets. It only proposes governed experience updates."])
    return "\n".join(lines) + "\n"


def build_reflect_ci_check(
    report: dict[str, Any],
    *,
    max_findings: int | None = None,
    max_high_severity: int | None = 0,
    max_generated_proposals: int | None = None,
) -> dict[str, Any]:
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    proposals = report.get("generated_proposals") if isinstance(report.get("generated_proposals"), list) else []
    high_findings = [item for item in findings if isinstance(item, dict) and item.get("severity") == "high"]
    failures: list[dict[str, Any]] = []
    if max_findings is not None and len(findings) > max_findings:
        failures.append(
            {
                "code": "reflect_findings_over_limit",
                "message": "Reflect findings exceed the configured CI limit.",
                "count": len(findings),
                "allowed": max_findings,
            }
        )
    if max_high_severity is not None and len(high_findings) > max_high_severity:
        failures.append(
            {
                "code": "reflect_high_severity_over_limit",
                "message": "High-severity Reflect findings exceed the configured CI limit.",
                "count": len(high_findings),
                "allowed": max_high_severity,
            }
        )
    if max_generated_proposals is not None and len(proposals) > max_generated_proposals:
        failures.append(
            {
                "code": "reflect_generated_proposals_over_limit",
                "message": "Reflect generated proposals exceed the configured CI limit.",
                "count": len(proposals),
                "allowed": max_generated_proposals,
            }
        )
    passed = not failures
    return {
        "passed": passed,
        "exit_code": 0 if passed else 1,
        "policy": {
            "max_findings": max_findings,
            "max_high_severity": max_high_severity,
            "max_generated_proposals": max_generated_proposals,
        },
        "counts": {
            "findings": len(findings),
            "high_severity_findings": len(high_findings),
            "generated_proposals": len(proposals),
        },
        "failures": failures,
    }


def format_reflect_ci_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    ci = report.get("ci") if isinstance(report.get("ci"), dict) else {}
    status = "passed" if ci.get("passed", True) else "failed"
    lines = [
        "## Praxile Reflect CI",
        "",
        f"- Status: {status}",
        f"- Reflect ID: `{report.get('reflect_id')}`",
        f"- Runs: {summary.get('runs', 0)}",
        f"- Assets: {summary.get('assets', 0)}",
        f"- Findings: {summary.get('findings', 0)}",
        f"- Generated proposals: {summary.get('generated_proposals', 0)}",
        f"- No active assets modified: {report.get('no_assets_modified', True)}",
    ]
    report_path = report.get("report_path")
    if report_path:
        lines.append(f"- Stored report: `{report_path}`")
    counts = summary.get("finding_counts") or {}
    if counts:
        lines.extend(["", "### Findings By Type"])
        for key, value in counts.items():
            lines.append(f"- `{key}`: {value}")
    failures = ci.get("failures") or []
    if failures:
        lines.extend(["", "### CI Failures"])
        for failure in failures:
            if isinstance(failure, dict):
                lines.append(f"- `{failure.get('code')}`: {failure.get('message')} ({failure.get('count')} > {failure.get('allowed')})")
    lines.extend(["", "Reflect is proposal-governed and does not rewrite memory directly."])
    return "\n".join(lines) + "\n"


def _summary(findings: list[dict[str, Any]], proposals: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    finding_counts: dict[str, int] = {}
    for item in findings:
        finding_counts[str(item.get("type") or "unknown")] = finding_counts.get(str(item.get("type") or "unknown"), 0) + 1
    proposal_counts: dict[str, int] = {}
    for item in proposals:
        proposal_counts[str(item.get("type") or "unknown")] = proposal_counts.get(str(item.get("type") or "unknown"), 0) + 1
    return {
        "runs": len(context["runs"]),
        "assets": len(context["assets"]),
        "proposals": len(context["proposals"]),
        "feedback": len(context["feedback"]),
        "silent_failure_signals": len(context["silent_failure_signals"]),
        "findings": len(findings),
        "generated_proposals": len(proposals),
        "finding_counts": dict(sorted(finding_counts.items())),
        "proposal_counts": dict(sorted(proposal_counts.items())),
    }


def _selected_asset_summary(context: dict[str, Any], selected_asset: str) -> dict[str, Any]:
    for asset in context["assets"]:
        if asset.get("path") != selected_asset:
            continue
        return {
            "found": True,
            "path": asset.get("path"),
            "type": asset.get("type"),
            "title": asset.get("title"),
            "status": asset.get("status", "active"),
            "usage_count": asset.get("usage_count", 0),
            "positive_outcome_count": asset.get("positive_outcome_count", 0),
            "negative_outcome_count": asset.get("negative_outcome_count", 0),
            "last_used_at": asset.get("last_used_at"),
            "source_task_id": asset.get("source_task_id"),
            "created_at": asset.get("created_at"),
            "updated_at": asset.get("updated_at"),
        }
    return {"found": False, "path": selected_asset}


def _compact_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": run.get("task_id"),
        "status": (run.get("result") or {}).get("status"),
        "reward_overall": (run.get("reward_report") or {}).get("overall"),
        "silent_failure_count": len(run.get("silent_failure_signals") or []),
    }


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "title": proposal.get("title"),
        "risk_level": proposal.get("risk_level"),
        "confidence": proposal.get("confidence"),
        "target_files": proposal.get("target_files") or [],
        "proposal_gate": proposal.get("proposal_gate") or {},
    }


def _active_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in assets if str(item.get("status") or "active") == "active"]


def _duplicate_key(asset: dict[str, Any]) -> str:
    title = str(asset.get("title") or "").strip()
    if title:
        return slugify(title, max_length=56)
    terms = _tokens(str(asset.get("summary") or ""))
    return "-".join(terms[:8])


def _tokens(text: str) -> list[str]:
    stop = {"the", "and", "for", "with", "this", "that", "from", "when", "then", "into", "should", "must"}
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", text.lower()):
        token = token.strip("_-")
        if len(token) < 3 or token in stop:
            continue
        if token not in terms:
            terms.append(token)
    return terms


def _merge_note(reflect_id: str, finding: dict[str, Any], items: list[dict[str, Any]], canonical_path: str) -> str:
    lines = [
        "## Reflect Duplicate Consolidation Evidence",
        "",
        f"- Reflect run: `{reflect_id}`",
        f"- Finding: `{finding.get('finding_id')}`",
        f"- Canonical asset: `{canonical_path}`",
        f"- Reason: {finding.get('reason')}",
        "",
        "### Overlapping Sources",
        "",
    ]
    for item in items:
        lines.append(
            f"- `{item.get('path')}` type={item.get('type')} usage={item.get('usage_count', 0)} "
            f"positive={item.get('positive_outcome_count', 0)} negative={item.get('negative_outcome_count', 0)}"
        )
    lines.extend(
        [
            "",
            "### Review Note",
            "",
            "This append preserves why the duplicate merge was proposed. It does not delete superseded assets.",
            "",
        ]
    )
    return "\n".join(lines)


def _rejection_key(proposal: dict[str, Any]) -> str:
    reason = str(proposal.get("rejection_reason") or "").strip().lower()
    if reason:
        terms = _tokens(reason)
        if terms:
            return "-".join(terms[:6])
    feedback = proposal.get("user_feedback") if isinstance(proposal.get("user_feedback"), dict) else {}
    if int(feedback.get("negative_count") or 0) > 0:
        return f"{proposal.get('type', 'proposal')}-negative-feedback"
    return str(proposal.get("type") or "proposal")


def _finding_id(prefix: str, value: str) -> str:
    return f"finding_{prefix}_{stable_hash(value, length=10)}"


def _normalize_asset_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().lower()
    now = datetime.now(timezone.utc)
    if text.endswith("d") and text[:-1].isdigit():
        return now - timedelta(days=int(text[:-1]))
    if text.endswith("h") and text[:-1].isdigit():
        return now - timedelta(hours=int(text[:-1]))
    parsed = _parse_timestamp(value)
    if parsed:
        return parsed
    raise ValueError(f"Invalid --since value: {value}")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _after_cutoff(value: Any, cutoff: datetime) -> bool:
    timestamp = _parse_timestamp(value)
    return bool(timestamp is None or timestamp >= cutoff)
