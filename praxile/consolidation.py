from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .evolution import EvolutionEngine
from .store import ExperienceStore
from .utils import shorten, slugify


class ConsolidationEngine:
    """Create proposal-only cleanup suggestions for accumulated experience assets."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.evolution = EvolutionEngine(config)

    def generate(
        self,
        *,
        duplicates: bool = True,
        stale: bool = False,
        conflicts: bool = False,
        low_value: bool = False,
        stale_days: int | None = None,
    ) -> list[dict[str, Any]]:
        self.store.reindex_all()
        findings: dict[str, list[dict[str, Any]]] = {}
        if duplicates:
            findings["duplicates"] = self._duplicate_groups()
        if stale:
            findings["stale"] = self._stale_assets(days=stale_days)
        if conflicts:
            findings["conflicts"] = self._conflict_groups()
        if low_value:
            findings["low_value"] = self._low_value_assets()
        findings = {key: value for key, value in findings.items() if value}
        if not findings:
            return []
        proposals: list[dict[str, Any]] = []
        for group in findings.get("duplicates", [])[:3]:
            proposal = self._asset_merge_proposal(group)
            if proposal:
                proposals.append(proposal)
        for item in (findings.get("low_value", []) + findings.get("stale", []))[:8]:
            if str(item.get("status") or "active") == "deprecated":
                proposal = self._asset_archive_proposal(item, reason="deprecated stale consolidation finding")
            elif self._should_rewrite_asset(item):
                proposal = self._asset_rewrite_proposal(item, reason="low-value asset has enough positive signal to preserve after rewrite")
            else:
                proposal = self._asset_deprecate_proposal(item, reason="low-value or stale consolidation finding")
            if proposal:
                proposals.append(proposal)
        if proposals and not findings.get("conflicts"):
            return proposals
        title = slugify("experience-consolidation", max_length=48)
        lines = [
            f"# Experience Consolidation: {title}",
            "",
            "This is a proposal-only cleanup note. Praxile does not merge, delete, or rewrite experience assets automatically.",
            "",
        ]
        evidence: list[str] = []
        if findings.get("duplicates"):
            lines.extend(["## Duplicate Or Overlapping Groups", ""])
            for group in findings["duplicates"][:8]:
                lines.append(f"### {group['key']}")
                lines.append("")
                for item in group["items"]:
                    lines.append(self._asset_line(item))
                lines.append("")
                evidence.append(f"{len(group['items'])} assets share normalized key `{group['key']}`.")
        if findings.get("stale"):
            lines.extend(["## Stale Assets", ""])
            for item in findings["stale"][:12]:
                lines.append(self._asset_line(item))
            lines.append("")
            evidence.append(f"{len(findings['stale'])} asset(s) appear stale by age and usage metadata.")
        if findings.get("conflicts"):
            lines.extend(["## Possible Conflicts", ""])
            for group in findings["conflicts"][:8]:
                lines.append(f"### {group['key']}")
                for item in group["items"]:
                    lines.append(self._asset_line(item))
                lines.append("")
                evidence.append(f"{len(group['items'])} assets may conflict under normalized key `{group['key']}`.")
        if findings.get("low_value"):
            lines.extend(["## Low-Value Assets", ""])
            for item in findings["low_value"][:12]:
                lines.append(self._asset_line(item))
            lines.append("")
            evidence.append(f"{len(findings['low_value'])} asset(s) have weak confidence or poor usage outcome signals.")
        lines.extend(
            [
                "## Suggested Human Decision",
                "",
                "- Keep the clearest source-linked asset as canonical.",
                "- Deprecate or rewrite duplicates only through separate reviewed proposals.",
                "- Mark stale or low-value assets as deprecated before deleting anything.",
                "- Resolve conflicts by writing a new explicit decision or harness rule proposal.",
                "- Preserve source task IDs and confidence fields when consolidating.",
            ]
        )
        proposal = self.evolution._proposal(
            source_task_id="manual_consolidation",
            proposal_type="experience_consolidation",
            title="Review experience asset governance findings",
            reason="Repeated, stale, conflicting, or low-value local experience assets can pollute retrieval if left unconsolidated.",
            risk_level="low",
            evidence=evidence,
            confidence=0.7,
            trigger_reason="Manual consolidation scan requested.",
            future_applicability="Local Praxile experience maintenance; proposal-only, no automatic merges.",
            applicability_scope="Local Praxile experience maintenance; proposal-only, no automatic merges.",
            anti_scope="Do not delete, merge, or deprecate source assets without a follow-up reviewed proposal.",
            changes=[
                {
                    "path": "memory/decisions.md",
                    "operation": "append",
                    "content": "\n".join(lines) + "\n",
                }
            ],
        )
        proposals.append(proposal)
        return proposals

    def summary(
        self,
        *,
        duplicates: bool = True,
        stale: bool = False,
        conflicts: bool = False,
        low_value: bool = False,
        stale_days: int | None = None,
    ) -> dict[str, int]:
        self.store.reindex_all()
        return {
            "duplicates": len(self._duplicate_groups()) if duplicates else 0,
            "stale": len(self._stale_assets(days=stale_days)) if stale else 0,
            "conflicts": len(self._conflict_groups()) if conflicts else 0,
            "low_value": len(self._low_value_assets()) if low_value else 0,
        }

    def _duplicate_groups(self) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for kind in ["memory", "failure", "eval", "skill"]:
            assets.extend(self.store.list_assets(kind))
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in assets:
            key = self._key(item)
            if key:
                grouped[key].append(item)
        duplicates = [
            {"key": key, "items": items}
            for key, items in grouped.items()
            if len(items) >= int(self.config.get("evolution", "consolidation_min_duplicates", default=2))
        ]
        duplicates.sort(key=lambda group: (-len(group["items"]), group["key"]))
        return duplicates

    def _stale_assets(self, *, days: int | None = None) -> list[dict[str, Any]]:
        stale_days = int(days or self.config.get("evolution", "consolidation_stale_days", default=90) or 90)
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
        stale: list[dict[str, Any]] = []
        for item in self._governed_assets():
            usage_count = int(item.get("usage_count") or 0)
            if usage_count > 0:
                continue
            timestamp = _parse_timestamp(item.get("updated_at") or item.get("created_at"))
            if timestamp and timestamp <= cutoff:
                stale.append(item)
        stale.sort(key=lambda item: (str(item.get("updated_at") or ""), item.get("path", "")))
        return stale

    def _conflict_groups(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self._governed_assets():
            key = self._key(item)
            if key:
                grouped[key].append(item)
        conflicts: list[dict[str, Any]] = []
        for key, items in grouped.items():
            types = {item.get("type") for item in items}
            statuses = {str(item.get("status") or "active") for item in items}
            summaries = " ".join(str(item.get("summary") or "").lower() for item in items)
            language_conflict = any(token in summaries for token in ["must not", "never", "禁止", "不要"]) and any(
                token in summaries for token in ["must ", "always", "require", "必须", "总是"]
            )
            if len(types) > 1 or len(statuses) > 1 or language_conflict:
                conflicts.append({"key": key, "items": items})
        conflicts.sort(key=lambda group: (-len(group["items"]), group["key"]))
        return conflicts

    def _low_value_assets(self) -> list[dict[str, Any]]:
        max_confidence = float(self.config.get("evolution", "consolidation_low_value_max_confidence", default=0.4) or 0.4)
        results: list[dict[str, Any]] = []
        for item in self._governed_assets():
            confidence = item.get("confidence")
            negative = int(item.get("negative_outcome_count") or 0)
            positive = int(item.get("positive_outcome_count") or 0)
            usage = int(item.get("usage_count") or 0)
            low_confidence = confidence is not None and float(confidence) <= max_confidence
            poor_outcome = negative >= 2 and negative > positive
            never_used_weak_memory = usage == 0 and item.get("type") == "memory" and low_confidence
            if low_confidence or poor_outcome or never_used_weak_memory:
                results.append(item)
        results.sort(
            key=lambda item: (
                float(item.get("confidence") if item.get("confidence") is not None else 1.0),
                -int(item.get("negative_outcome_count") or 0),
                item.get("path", ""),
            )
        )
        return results

    def _governed_assets(self) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for kind in ["memory", "failure", "eval", "skill", "rule"]:
            assets.extend(self.store.list_assets(kind))
        return assets

    def _key(self, item: dict[str, Any]) -> str:
        title = str(item.get("title") or "")
        if not title:
            return ""
        return slugify(title, max_length=64)

    def _asset_line(self, item: dict[str, Any]) -> str:
        usage = item.get("usage_count", 0)
        confidence = item.get("confidence")
        updated_at = item.get("updated_at")
        return (
            f"- `{item['path']}` ({item['type']}, usage={usage}, confidence={confidence}, updated={updated_at}): "
            f"{shorten(item.get('summary') or '', 160)}"
        )

    def _asset_deprecate_proposal(self, item: dict[str, Any], *, reason: str) -> dict[str, Any] | None:
        path = str(item.get("path") or "")
        if not path or item.get("status") != "active":
            return None
        target = path.removeprefix(".praxile/")
        return self.evolution._proposal(
            source_task_id="manual_consolidation",
            proposal_type="asset_deprecate",
            title=f"Deprecate low-value asset `{target}`",
            reason="Consolidation found weak confidence, poor outcomes, or stale unused experience that can pollute retrieval.",
            risk_level="low",
            evidence=[
                f"Asset `{path}` status={item.get('status')} usage={item.get('usage_count')} "
                f"positive={item.get('positive_outcome_count')} negative={item.get('negative_outcome_count')}.",
                f"Reason: {reason}.",
            ],
            confidence=0.7,
            trigger_reason="Consolidation low-value/stale scan.",
            future_applicability="Experience governance only; accepting changes retrieval by marking this asset deprecated.",
            applicability_scope="The specified project-local asset only.",
            anti_scope="Do not delete source files automatically; deprecation can be rolled back.",
            changes=[
                {
                    "path": target,
                    "operation": "metadata_update",
                    "metadata": {
                        "status": "deprecated",
                        "deprecated_reason": reason,
                        "deprecated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            ],
        )

    def _asset_archive_proposal(self, item: dict[str, Any], *, reason: str) -> dict[str, Any] | None:
        path = str(item.get("path") or "")
        if not path or item.get("status") == "archived":
            return None
        target = path.removeprefix(".praxile/")
        return self.evolution._proposal(
            source_task_id="manual_consolidation",
            proposal_type="asset_archive",
            title=f"Archive retired asset `{target}`",
            reason="Consolidation found an already-retired asset that appears stale enough to archive.",
            risk_level="low",
            evidence=[
                f"Asset `{path}` status={item.get('status')} usage={item.get('usage_count')} "
                f"positive={item.get('positive_outcome_count')} negative={item.get('negative_outcome_count')}.",
                f"Reason: {reason}.",
            ],
            confidence=0.75,
            trigger_reason="Consolidation stale retired asset scan.",
            future_applicability="Experience governance only; accepting archives this asset and excludes it from retrieval.",
            applicability_scope="The specified project-local asset only.",
            anti_scope="Does not delete the original asset file; archive can be rolled back or reactivated.",
            changes=[
                {
                    "path": target,
                    "operation": "metadata_update",
                    "metadata": {
                        "status": "archived",
                        "archived_reason": reason,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            ],
        )

    def _asset_rewrite_proposal(self, item: dict[str, Any], *, reason: str) -> dict[str, Any] | None:
        path = str(item.get("path") or "")
        if not path or item.get("status") != "active":
            return None
        target = path.removeprefix(".praxile/")
        content = self._rewrite_content(item, reason=reason)
        return self.evolution._proposal(
            source_task_id="manual_consolidation",
            proposal_type="asset_rewrite",
            title=f"Rewrite weak experience asset `{target}`",
            reason="Consolidation found a useful but underspecified asset; rewrite it instead of deprecating it.",
            risk_level="medium",
            evidence=[
                f"Asset `{path}` usage={item.get('usage_count')} positive={item.get('positive_outcome_count')} "
                f"negative={item.get('negative_outcome_count')} confidence={item.get('confidence')}.",
                f"Reason: {reason}.",
            ],
            confidence=0.62,
            trigger_reason="Consolidation low-value scan found salvageable positive outcome evidence.",
            future_applicability="Experience governance only; accepting rewrites the targeted asset content.",
            applicability_scope="The specified project-local asset only.",
            anti_scope="Do not accept without reading the full diff; content-level rewrites are stronger than metadata changes.",
            changes=[{"path": target, "operation": "write", "content": content}],
        )

    def _asset_merge_proposal(self, group: dict[str, Any]) -> dict[str, Any] | None:
        items = list(group.get("items") or [])
        if len(items) < 2:
            return None
        canonical = sorted(items, key=lambda item: (-int(item.get("positive_outcome_count") or 0), item.get("path", "")))[0]
        canonical_path = str(canonical.get("path") or "")
        if not canonical_path:
            return None
        changes: list[dict[str, Any]] = []
        evidence = [f"{len(items)} assets share normalized key `{group.get('key')}`."]
        for item in items:
            path = str(item.get("path") or "")
            if not path or path == canonical_path or item.get("status") != "active":
                continue
            changes.append(
                {
                    "path": path.removeprefix(".praxile/"),
                    "operation": "metadata_update",
                    "metadata": {
                        "status": "superseded",
                        "replaced_by": canonical_path,
                        "superseded_reason": f"Merged into canonical asset `{canonical_path}`.",
                        "superseded_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            )
            evidence.append(f"`{path}` is superseded by `{canonical_path}`.")
        enhancement = self._canonical_merge_enhancement(canonical, items)
        if enhancement:
            changes.append(
                {
                    "path": canonical_path.removeprefix(".praxile/"),
                    "operation": "append",
                    "content": enhancement,
                }
            )
            evidence.append(f"Canonical asset `{canonical_path}` receives consolidated source evidence.")
        if not changes:
            return None
        return self.evolution._proposal(
            source_task_id="manual_consolidation",
            proposal_type="asset_merge",
            title=f"Supersede duplicate experience assets for `{group.get('key')}`",
            reason="Consolidation found duplicate or overlapping local experience; keep one canonical asset and supersede the rest.",
            risk_level="low",
            evidence=evidence,
            confidence=0.72,
            trigger_reason="Consolidation duplicate scan.",
            future_applicability="Experience governance only; accepting updates asset lifecycle metadata and retrieval.",
            applicability_scope="Duplicate assets in this project-local experience group.",
            anti_scope="Does not delete source assets; canonical content is only appended with reviewed consolidation evidence.",
            changes=changes,
        )

    def _should_rewrite_asset(self, item: dict[str, Any]) -> bool:
        return (
            str(item.get("status") or "active") == "active"
            and int(item.get("positive_outcome_count") or 0) > 0
            and int(item.get("negative_outcome_count") or 0) <= int(item.get("positive_outcome_count") or 0)
            and item.get("type") in {"memory", "failure_pattern", "eval_checklist", "eval_case"}
        )

    def _rewrite_content(self, item: dict[str, Any], *, reason: str) -> str:
        title = str(item.get("title") or "Experience Asset")
        summary = str(item.get("summary") or "").strip()
        return (
            f"# {title}\n\n"
            "## Consolidated Guidance\n\n"
            f"- Source asset: `{item.get('path')}`\n"
            f"- Rewrite reason: {reason}\n"
            f"- Usage outcomes: positive={item.get('positive_outcome_count', 0)} "
            f"negative={item.get('negative_outcome_count', 0)} usage={item.get('usage_count', 0)}\n\n"
            "## Applies When\n\n"
            "- Future work matches the same files, commands, failure signatures, or project area cited by this asset.\n\n"
            "## Does Not Apply When\n\n"
            "- The future task touches unrelated architecture, security, UI, runtime, or data-flow constraints.\n"
            "- A newer accepted asset supersedes this guidance.\n\n"
            "## Evidence To Preserve\n\n"
            f"{summary or '- Original asset did not contain enough summary detail.'}\n"
        )

    def _canonical_merge_enhancement(self, canonical: dict[str, Any], items: list[dict[str, Any]]) -> str:
        source_lines = []
        applies = set()
        anti_scope = set()
        signatures = set()
        verification = set()
        fix_actions = set()
        for item in items:
            path = str(item.get("path") or "")
            if not path or path == canonical.get("path"):
                continue
            source_lines.append(
                f"- `{path}` usage={item.get('usage_count', 0)} "
                f"positive={item.get('positive_outcome_count', 0)} negative={item.get('negative_outcome_count', 0)}"
            )
            summary = str(item.get("summary") or "")
            applies.update(_section_bullets(summary, ["applies_when", "applies when", "when to use"]))
            anti_scope.update(_section_bullets(summary, ["does_not_apply_when", "does not apply when", "anti_scope", "anti-scope"]))
            signatures.update(_inline_values(summary, ["failure_signature", "signature"]))
            verification.update(
                line
                for line in _section_bullets(summary, ["verification", "verification_commands", "verification commands"])
                if any(token in line.lower() for token in ["pytest", "npm", "go test", "cargo test", "python -m"])
            )
            fix_actions.update(_section_bullets(summary, ["fix_actions", "fix actions", "fix strategy"]))
        if not source_lines:
            return ""
        canonical_summary = str(canonical.get("summary") or "")
        applies.update(_section_bullets(canonical_summary, ["applies_when", "applies when", "when to use"]))
        anti_scope.update(_section_bullets(canonical_summary, ["does_not_apply_when", "does not apply when", "anti_scope", "anti-scope"]))
        signatures.update(_inline_values(canonical_summary, ["failure_signature", "signature"]))
        verification.update(_section_bullets(canonical_summary, ["verification", "verification_commands", "verification commands"]))
        fix_actions.update(_section_bullets(canonical_summary, ["fix_actions", "fix actions", "fix strategy"]))
        return (
            "## Consolidated Duplicate Evidence\n\n"
            "This asset was selected as the canonical version during consolidation. Superseded sources:\n\n"
            + "\n".join(source_lines)
            + "\n\n"
            "### Merged applies_when\n\n"
            + _bullet_block(applies, fallback="- Future work matches the same files, commands, failure signatures, or project area cited by the duplicate sources.")
            + "\n\n"
            "### Merged does_not_apply_when\n\n"
            + _bullet_block(anti_scope, fallback="- Future work has different architecture, security, UI, runtime, or data-flow constraints.")
            + "\n\n"
            "### Merged failure_signature\n\n"
            + _bullet_block(signatures, fallback="- `(not recorded)`")
            + "\n\n"
            "### Merged verification_commands\n\n"
            + _bullet_block(verification, fallback="- Re-run the source asset's narrow verification command before relying on this merged guidance.")
            + "\n\n"
            "### Merged fix_actions\n\n"
            + _bullet_block(fix_actions, fallback="- Preserve the concrete fix actions from accepted source runs when future trajectories provide them.")
            + "\n\n"
            "### Outcome Summary\n\n"
            f"- Canonical `{canonical.get('path')}` usage={canonical.get('usage_count', 0)} "
            f"positive={canonical.get('positive_outcome_count', 0)} negative={canonical.get('negative_outcome_count', 0)}\n"
            "Keep this canonical asset active only while it remains more specific and better-evidenced than the retired sources.\n"
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _section_bullets(text: str, headings: list[str]) -> set[str]:
    lines = str(text or "").splitlines()
    wanted = {heading.lower().replace(" ", "_").replace("-", "_") for heading in headings}
    collecting = False
    values: set[str] = set()
    for line in lines:
        stripped = line.strip()
        normalized_heading = stripped.strip("#: `").lower().replace(" ", "_").replace("-", "_")
        if stripped.startswith("#"):
            collecting = any(heading in normalized_heading for heading in wanted)
            continue
        if collecting and stripped.startswith("-"):
            value = stripped.strip("- ").strip()
            if value and len(value) > 5:
                values.add(value)
        elif collecting and stripped.startswith("##"):
            collecting = False
    return values


def _inline_values(text: str, keys: list[str]) -> set[str]:
    values: set[str] = set()
    for key in keys:
        pattern = rf"{re.escape(key)}\s*[:=]\s*`?([^`\n]+)`?"
        for match in re.findall(pattern, str(text or ""), flags=re.IGNORECASE):
            value = match.strip(" `\"'")
            if value and value.lower() not in {"unknown", "not recorded", "(not recorded)"}:
                values.add(f"`{value}`")
    return values


def _bullet_block(values: set[str], *, fallback: str) -> str:
    cleaned = sorted(value for value in values if value.strip())
    if not cleaned:
        return fallback
    return "\n".join(value if value.startswith("-") else f"- {value}" for value in cleaned[:8])
