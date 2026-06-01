from __future__ import annotations

import math
import shutil
import sqlite3
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ..config import Config, ProjectPaths
from ..constants import PRAXILE_DIR
from ..feedback import feedback_reward
from ..interop import PRAXILE_TRAJECTORY_SCHEMA, EXTERNAL_COMPAT_TRAJECTORY_FORMAT
from ..snapshot import SnapshotManager
from ..utils import append_jsonl, file_lock, path_is_relative_to, read_json, shorten, stable_hash, utc_now, write_json
from ..vector import cosine_similarity, embed_text, vector_settings


MEMORY_FILES = {
    "user": "User preferences and stable working habits.",
    "project": "Project facts, architecture notes, common commands, and stable context.",
    "decisions": "Accepted decisions with source task IDs and rationale.",
    "failures": "Failure patterns and repair notes that should not be repeated blindly.",
}

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "templates" / "state"

PROPOSAL_ROOTS = {"memory", "skills", "evals", "rules", "experience"}
PROPOSAL_RULE_ROOTS = {"architecture-gates", "frozen-boundaries", "harness-rules"}
PROPOSAL_EVAL_ROOTS = {"checklists", "regression-cases"}
PROPOSAL_EXPERIENCE_ROOTS = {"failures", "patterns"}
ASSET_TYPE_PRIORITY = {
    "frozen_boundary": 0,
    "architecture_gate": 0,
    "harness_rule": 0,
    "rule": 0,
    "skill": 1,
    "eval_checklist": 2,
    "eval_case": 2,
    "project_pattern": 2,
    "failure_pattern": 3,
    "memory": 4,
    "trajectory_summary": 5,
}
ASSET_ACTIVE_STATUSES = {"active"}
ASSET_LIFECYCLE_STATUSES = {"active", "deprecated", "superseded", "archived"}


def _graph_asset_node_id(path: str) -> str:
    return f"asset:{_normalize_graph_asset_path(path)}"


def _graph_run_node_id(task_id: str) -> str:
    return f"run:{str(task_id or '').strip()}"


def _graph_proposal_node_id(proposal_id: str) -> str:
    return f"proposal:{str(proposal_id or '').strip()}"


def _graph_executor_node_id(task_id: str, executor_id: str) -> str:
    return f"executor:{str(task_id or '').strip()}:{str(executor_id or '').strip()}"


def _graph_spec_node_id(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    return f"spec:{text}"


def _normalize_graph_asset_path(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    while text.startswith(f"{PRAXILE_DIR}/{PRAXILE_DIR}/"):
        text = text[len(PRAXILE_DIR) + 1 :]
    if not text.startswith(f"{PRAXILE_DIR}/"):
        text = f"{PRAXILE_DIR}/{text.lstrip('/')}"
    return text


def _graph_confidence(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if math.isnan(parsed) or math.isinf(parsed):
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 4)


def _graph_node_candidates(ref: str) -> list[str]:
    text = str(ref or "").strip()
    if not text:
        return []
    candidates = []
    if text.startswith(("asset:", "run:", "proposal:", "spec:")):
        candidates.append(text)
        bare = text.split(":", 1)[1]
    else:
        bare = text
    candidates.extend(
        [
            _graph_asset_node_id(bare),
            _graph_proposal_node_id(bare),
            _graph_run_node_id(bare),
            _graph_spec_node_id(bare),
        ]
    )
    normalized = _normalize_graph_asset_path(bare)
    if normalized != bare:
        candidates.append(_graph_asset_node_id(normalized))
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def _graph_node_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    return {
        "node_id": item.get("node_id"),
        "node_type": item.get("node_type"),
        "ref_path": item.get("ref_path"),
        "title": item.get("title"),
        "created_at": item.get("created_at"),
    }


def _graph_edge_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    evidence = item.get("evidence")
    if isinstance(evidence, str) and evidence:
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            pass
    return {
        "edge_id": item.get("edge_id"),
        "source_node_id": item.get("source_node_id"),
        "target_node_id": item.get("target_node_id"),
        "relation_type": item.get("relation_type"),
        "confidence": item.get("confidence"),
        "evidence": evidence if evidence is not None else {},
        "created_at": item.get("created_at"),
    }


def _count_graph_edges(edges: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges or []:
        relation = edge.get("relation_type") if isinstance(edge, dict) else None
        if not relation:
            continue
        key = str(relation)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _graph_conflict_pairs(proposal: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    pairs: list[tuple[str, str, dict[str, Any]]] = []
    for field in ["conflicts", "contradictions", "conflicting_assets"]:
        raw_items = proposal.get(field)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            left = right = ""
            evidence: dict[str, Any] = {"field": field}
            if isinstance(item, dict):
                left = str(
                    item.get("left")
                    or item.get("source")
                    or item.get("source_path")
                    or item.get("asset")
                    or item.get("asset_path")
                    or ""
                )
                right = str(
                    item.get("right")
                    or item.get("target")
                    or item.get("target_path")
                    or item.get("other")
                    or item.get("other_path")
                    or ""
                )
                evidence.update({key: value for key, value in item.items() if key not in {"left", "right", "source", "target"}})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                left = str(item[0])
                right = str(item[1])
            if left and right and left != right:
                pairs.append((_normalize_graph_asset_path(left), _normalize_graph_asset_path(right), evidence))
    return pairs


def _attribution_allows_outcome_update(attribution: dict[str, Any], *, success: bool) -> bool:
    if attribution.get("should_update_asset_outcome") is False:
        return False
    level = _normalize_attribution_level(attribution.get("attribution_level"))
    if success:
        return level in {"weak_positive", "strong_positive", "mixed"}
    return level in {"weak_negative", "harmful", "mixed"}


def _usage_attribution_level(item: dict[str, Any]) -> str:
    outcome = str(item.get("outcome") or "unknown")
    if item.get("used_explicitly"):
        return "strong_positive" if outcome == "success" else "harmful" if outcome == "failed" else "referenced"
    if item.get("referenced"):
        return "weak_positive" if outcome == "success" else "weak_negative" if outcome == "failed" else "referenced"
    if item.get("used_in_prompt"):
        return "loaded_only" if outcome in {"success", "failed", "unknown", "needs_human"} else "loaded_only"
    return "none"


def _normalize_attribution_level(value: Any) -> str:
    text = str(value or "uncertain").strip().lower()
    aliases = {
        "medium_positive": "weak_positive",
        "medium_negative": "weak_negative",
        "strong_negative": "harmful",
        "explicit_unknown": "referenced",
        "referenced_unknown": "referenced",
    }
    return aliases.get(text, text)


def _proposal_feedback_terms(proposal: dict[str, Any]) -> list[str]:
    values: list[str] = [
        str(proposal.get("type") or ""),
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
    return list(dict.fromkeys(terms))[:24]


def _lifecycle_event_from_metadata(metadata: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    status = metadata.get("status")
    if not status:
        return None
    reason = (
        metadata.get("reactivated_reason")
        or metadata.get("deprecated_reason")
        or metadata.get("superseded_reason")
        or metadata.get("archived_reason")
        or metadata.get("reason")
    )
    return {
        "status": status,
        "reason": reason,
        "replaced_by": metadata.get("replaced_by"),
        "source": source,
        "at": metadata.get("updated_at") or utc_now(),
    }


def _value_from_row(row: sqlite3.Row | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key] if key in row.keys() else default
    except (KeyError, IndexError):
        return default


def _int_from_row(row: sqlite3.Row | dict[str, Any], key: str) -> int:
    try:
        return int(_value_from_row(row, key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _title_from_content(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
    return fallback


def _source_task_from_content(content: str) -> str | None:
    match = re.search(r"source[_ -]task(?:_id)?[:` ]+`?([A-Za-z0-9_-]+)`?", content, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _confidence_from_content(content: str) -> float | None:
    match = re.search(r"confidence[:` ]+`?([0-9.]+)`?", content, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _tags_for_asset(path: str, asset_type: str) -> str:
    parts = Path(path).parts
    tags = [asset_type]
    tags.extend(part for part in parts if part not in {PRAXILE_DIR, "SKILL.md"} and "." not in part)
    return ",".join(dict.fromkeys(tags))


def _fts_query(query: str) -> str:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query) if token.strip()]
    if not tokens:
        return ""
    safe_tokens = []
    for token in tokens[:12]:
        if re.fullmatch(r"[A-Za-z0-9_]+", token):
            safe_tokens.append(f"{token}*")
        else:
            safe_tokens.append(f'"{token}"')
    return " OR ".join(safe_tokens)


def _query_terms(query: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(term) > 1][:12]


def _retrieval_explanation(query: str, fields: dict[str, Any], *, mode: str, asset_type: str) -> dict[str, Any]:
    terms = _query_terms(query)
    matched_terms: list[str] = []
    matched_fields: list[str] = []
    for field_name, value in fields.items():
        lowered = str(value or "").lower()
        field_terms = [term for term in terms if term in lowered]
        if field_terms:
            normalized_field = "content" if field_name == "content" else field_name
            if normalized_field not in matched_fields:
                matched_fields.append(normalized_field)
            for term in field_terms:
                if term not in matched_terms:
                    matched_terms.append(term)
    if matched_terms and matched_fields:
        why = (
            f"matched task term(s) {', '.join(matched_terms[:5])} "
            f"in {', '.join(matched_fields)} for {asset_type} via {mode}"
        )
    else:
        why = f"loaded by {mode} score and {asset_type} priority"
    return {"matched_terms": matched_terms, "matched_fields": matched_fields, "why_loaded": why}


def _kind_from_asset_type(asset_type: str) -> str:
    if asset_type in {"frozen_boundary", "architecture_gate", "harness_rule", "rule"}:
        return "rule"
    if asset_type == "skill":
        return "skill"
    if asset_type in {"eval_checklist", "eval_case"}:
        return "eval"
    if asset_type == "memory":
        return "memory"
    if asset_type == "failure_pattern":
        return "failure"
    if asset_type == "project_pattern":
        return "pattern"
    return "trajectory"


def _asset_type_filter(kinds: list[str] | None) -> list[str]:
    if not kinds:
        return []
    expanded: list[str] = []
    mapping = {
        "rule": ["frozen_boundary", "architecture_gate", "harness_rule", "rule"],
        "skill": ["skill"],
        "eval": ["eval_checklist", "eval_case"],
        "memory": ["memory"],
        "failure": ["failure_pattern"],
        "pattern": ["project_pattern"],
        "experience": ["failure_pattern", "project_pattern"],
        "trajectory": ["trajectory_summary"],
    }
    for kind in kinds:
        expanded.extend(mapping.get(kind, [kind]))
    return list(dict.fromkeys(expanded))


def _decode_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = [name for name in globals() if not name.startswith("__")]
