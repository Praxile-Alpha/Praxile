"""Project-local preference proposals, distinct from capability evidence."""
from pathlib import PurePosixPath
import re


def is_preference_asset(asset):
    path = str(asset.get("path") or "").replace("\\", "/")
    parts = PurePosixPath(path).parts
    return parts[:3] == (".praxile", "memory", "preferences") or parts[:2] == ("memory", "preferences")


def usage_summary(assets):
    used = [asset for asset in assets if asset.get("used_in_prompt", True)]
    preferences = sum(is_preference_asset(asset) for asset in used)
    legacy = sum(str(asset.get("path") or "").replace("\\", "/") in
                 {".praxile/memory/user.md", "memory/user.md"} for asset in used)
    return {"schema_version": "praxile.experience_purpose_usage.v1",
            "personalization_assets_loaded": preferences,
            "legacy_user_memory_unclassified": legacy,
            "other_assets_loaded": len(used) - preferences - legacy,
            "personalization_effect": "not_measured", "capability_improvement": "not_measured"}


def compose_preference(config, *, key, text, applies_when, source_reference):
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key):
        raise ValueError("preference key must be a bounded lowercase identifier")
    for value in (text, applies_when, source_reference):
        if not isinstance(value, str) or not value.strip() or len(value) > 4000:
            raise ValueError("preference text, scope and source must be nonempty bounded text")
    from .evolution import EvolutionEngine
    content = (f"# Project-local user preference: {key}\n\n"
               "Purpose: personalization, not a verified capability or project fact.\n"
               "Authority: advisory only; never overrides task instructions or safety policy.\n\n"
               f"## Preference\n{text.strip()}\n\n## Applies When\n{applies_when.strip()}\n\n"
               f"## Source\n{source_reference.strip()}\n\n"
               "## Scope\nCurrent project only. No automatic global or cross-project transfer.\n")
    proposal = EvolutionEngine(config)._proposal(source_task_id="user_preference",
        proposal_type="memory_update", title=f"Confirm project preference: {key}",
        reason="Explicit preference proposal; requires user review, not inferred task success.",
        risk_level="low", changes=[{"path": f"memory/preferences/{key}.md", "operation": "write", "content": content}],
        evidence=[source_reference.strip()], confidence=0.0, applicability_scope=applies_when.strip(),
        anti_scope="Other projects, other users, conflicting task instructions or safety policies.",
        generated_by="explicit_preference_proposal")
    proposal.update(experience_purpose="personalization", preference_scope="project",
                    capability_improvement_claim=False,
                    source={"type": "user_preference", "reference": source_reference.strip()})
    proposal["evidence_items"] = [{"source": "user_preference", "summary": source_reference.strip()}]
    return proposal
