from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Config
from .store import ExperienceStore
from .utils import shorten, utc_now


def build_run_audit(
    config: Config,
    store: ExperienceStore,
    run_id: str,
    *,
    rebuild_graph: bool = False,
    redaction: str = "standard",
) -> dict[str, Any]:
    trajectory = store.latest_trajectory() if run_id == "latest" else store.get_trajectory(run_id)
    if not trajectory:
        return _finalize_report(_not_found_report(config, "run", run_id), redaction=redaction)
    task_id = str(trajectory.get("task_id") or run_id)
    trajectory_path = store.find_trajectory_path(task_id)
    if rebuild_graph:
        store.rebuild_experience_graph()
    proposals = _proposals_for_run(store, task_id, trajectory)
    usage = store.usage_for_task(task_id)
    report = trajectory.get("reward_report") if isinstance(trajectory.get("reward_report"), dict) else {}
    objective = report.get("objective_signals") if isinstance(report.get("objective_signals"), dict) else {}
    report = _base_report(config, "run", task_id) | {
        "found": True,
        "subject": {
            "task_id": task_id,
            "trajectory_path": _root_relative(config, trajectory_path) if trajectory_path else None,
            "user_task": trajectory.get("user_task"),
            "status": (trajectory.get("result") or {}).get("status"),
            "summary": (trajectory.get("result") or {}).get("summary"),
            "started_at": trajectory.get("start_time"),
            "ended_at": trajectory.get("end_time"),
        },
        "source_chain": {
            "spec_context": trajectory.get("spec_context") or {},
            "spec_compliance": trajectory.get("spec_compliance") or {},
            "loaded_assets": _compact_loaded_assets(trajectory.get("loaded_assets") or []),
            "asset_usage": _compact_usage(usage),
        },
        "decision_chain": {
            "task_analysis": trajectory.get("task_analysis") or {},
            "plan": trajectory.get("plan") or [],
            "model_route": (trajectory.get("model_routing") or {}).get("selected") or {},
            "actions": _compact_actions(trajectory.get("actions") or []),
            "executor_attribution": objective.get("executor_attribution") or {},
        },
        "reward_chain": {
            "overall": report.get("overall"),
            "objective_score_component": report.get("objective_score_component"),
            "final_reward": report.get("final_reward") or {},
            "experience_generation": report.get("experience_generation") or {},
            "manual_signals": report.get("manual_signals") or {},
            "notes": report.get("notes") or [],
        },
        "proposal_chain": {
            "proposal_count": len(proposals),
            "proposals": [_compact_proposal(item) for item in proposals],
            "proposal_gate_summary": trajectory.get("proposal_gate_summary") or {},
            "suppressed": trajectory.get("suppressed_experience_candidates") or [],
        },
        "graph": _graph_report(store, task_id),
    }
    return _finalize_report(report, redaction=redaction)


def build_proposal_audit(
    config: Config,
    store: ExperienceStore,
    proposal_id: str,
    *,
    rebuild_graph: bool = False,
    redaction: str = "standard",
) -> dict[str, Any]:
    proposal = store.find_proposal(proposal_id)
    if not proposal:
        return _finalize_report(_not_found_report(config, "proposal", proposal_id), redaction=redaction)
    proposal_id = str(proposal.get("proposal_id") or proposal_id)
    if rebuild_graph:
        store.rebuild_experience_graph()
    source_task_id = str(proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id") or "")
    source = store.get_trajectory(source_task_id) if source_task_id else None
    targets = _proposal_target_assets(store, proposal)
    report = _base_report(config, "proposal", proposal_id) | {
        "found": True,
        "subject": _compact_proposal(proposal) | {
            "created_at": proposal.get("created_at"),
            "updated_at": proposal.get("updated_at"),
            "generated_by": proposal.get("generated_by"),
        },
        "source_chain": {
            "source_task_id": source_task_id or None,
            "source_run": _compact_run(source) if source else None,
            "source": proposal.get("source") or {},
        },
        "evidence_chain": {
            "evidence_summary": proposal.get("evidence_summary"),
            "evidence": proposal.get("evidence") or [],
            "evidence_items": proposal.get("evidence_items") or [],
            "proposal_gate": proposal.get("proposal_gate") or {},
            "llm_judge": proposal.get("llm_judge") or {},
            "feedback_influence": proposal.get("feedback_influence") or [],
        },
        "review_chain": {
            "status": proposal.get("status"),
            "requires_user_approval": proposal.get("requires_user_approval", True),
            "requires_manual_review": proposal.get("requires_manual_review", True),
            "user_feedback": proposal.get("user_feedback") or {},
            "rejection_reason": proposal.get("rejection_reason"),
            "applied_changes": proposal.get("applied_changes") or [],
        },
        "asset_chain": {
            "target_files": proposal.get("target_files") or [],
            "target_assets": targets,
            "changes": _compact_changes(proposal.get("changes") or []),
        },
        "graph": _graph_report(store, proposal_id),
    }
    return _finalize_report(report, redaction=redaction)


def build_asset_audit(
    config: Config,
    store: ExperienceStore,
    asset_path: str,
    *,
    rebuild_graph: bool = False,
    redaction: str = "standard",
) -> dict[str, Any]:
    normalized = normalize_audit_asset_path(asset_path)
    asset = store.get_asset(normalized)
    if not asset:
        return _finalize_report(_not_found_report(config, "asset", asset_path) | {"normalized_ref": normalized}, redaction=redaction)
    if rebuild_graph:
        store.rebuild_experience_graph()
    source_task_id = str(asset.get("source_task_id") or "")
    source = store.get_trajectory(source_task_id) if source_task_id else None
    related_proposals = _proposals_for_asset(store, normalized)
    report = _base_report(config, "asset", normalized) | {
        "found": True,
        "subject": {
            "path": asset.get("path"),
            "type": asset.get("type"),
            "title": asset.get("title"),
            "status": asset.get("status", "active"),
            "confidence": asset.get("confidence"),
            "content_hash": asset.get("content_hash"),
            "size": asset.get("size"),
            "mtime_ns": asset.get("mtime_ns"),
        },
        "source_chain": {
            "source_task_id": source_task_id or None,
            "source_run": _compact_run(source) if source else None,
            "source_proposal": asset.get("source_proposal"),
        },
        "lifecycle_chain": {
            "status": asset.get("status", "active"),
            "replaced_by": asset.get("replaced_by"),
            "deprecated_reason": asset.get("deprecated_reason"),
            "superseded_reason": asset.get("superseded_reason"),
            "archived_reason": asset.get("archived_reason"),
            "reactivated_reason": asset.get("reactivated_reason"),
            "lifecycle_events": asset.get("lifecycle_events") or [],
        },
        "usage_chain": {
            "usage_count": asset.get("usage_count", 0),
            "positive_outcome_count": asset.get("positive_outcome_count", 0),
            "negative_outcome_count": asset.get("negative_outcome_count", 0),
            "last_used_at": asset.get("last_used_at"),
            "semantic_attribution_history": store.attribution_history_for_asset(normalized, limit=20),
        },
        "proposal_chain": {
            "related_proposal_count": len(related_proposals),
            "related_proposals": [_compact_proposal(item) for item in related_proposals],
        },
        "content_excerpt": shorten(str(asset.get("summary") or ""), 2000),
        "graph": _graph_report(store, normalized),
    }
    return _finalize_report(report, redaction=redaction)


def build_project_audit_bundle(
    config: Config,
    store: ExperienceStore,
    *,
    limit_runs: int = 20,
    rebuild_graph: bool = False,
    redaction: str = "standard",
    include_reflect: bool = False,
    reflect_limit: int = 5,
) -> dict[str, Any]:
    if rebuild_graph:
        store.rebuild_experience_graph()
    run_limit = max(1, int(limit_runs or 20))
    history = store.list_history(limit=run_limit)
    proposals = store.list_proposals(status=None, limit=10000)
    assets = _project_assets(store)
    graph_status = store.graph_status()
    reflect_reports = _latest_reflect_reports(config, limit=reflect_limit) if include_reflect else []
    pending = [item for item in proposals if item.get("status") == "pending"]
    high_risk_pending = [
        item
        for item in pending
        if item.get("risk_level") == "high" or str(item.get("priority", "")).lower() == "p0"
    ]
    report = _base_report(config, "bundle", "project") | {
        "found": True,
        "bundle": {
            "format": "praxile_project_audit_bundle_v1",
            "scope": "project_local_governed_experience",
            "read_only": True,
            "raw_secret_values": redaction == "none",
            "notes": [
                "This bundle summarizes local .praxile governance state.",
                "It does not accept, reject, edit, sync, or export assets to hidden global memory.",
            ],
        },
        "project": {
            "root": str(config.paths.root),
            "state_root": str(config.paths.state),
            "config_path": str(config.paths.config),
            "model_roles": sorted((config.get("model_roles", default={}) or {}).keys()),
            "provider_ids": sorted((config.get("model_providers", default={}) or {}).keys()),
        },
        "run_chain": {
            "limit": run_limit,
            "count": len(history),
            "runs": [_compact_history_row(item) for item in history],
        },
        "proposal_chain": {
            "count": len(proposals),
            "pending_count": len(pending),
            "high_risk_pending_count": len(high_risk_pending),
            "status_counts": _count_by(proposals, "status"),
            "type_counts": _count_by(proposals, "type"),
            "risk_counts": _count_by(proposals, "risk_level"),
            "pending": [_compact_proposal(item) for item in pending[:50]],
        },
        "asset_chain": {
            "count": len(assets),
            "status_counts": _count_by(assets, "status"),
            "type_counts": _count_by(assets, "type"),
            "assets": [_compact_asset(item) for item in assets[:200]],
        },
        "graph_chain": {
            "status": graph_status,
        },
        "reflect_chain": {
            "included": bool(include_reflect),
            "count": len(reflect_reports),
            "reports": reflect_reports,
        },
        "governance_summary": {
            "ready_for_release_review": not high_risk_pending,
            "recommended_actions": _bundle_recommended_actions(pending, high_risk_pending, graph_status),
        },
    }
    return _finalize_report(report, redaction=redaction)


def build_project_audit_check(
    config: Config,
    store: ExperienceStore,
    *,
    limit_runs: int = 20,
    rebuild_graph: bool = False,
    max_pending: int | None = None,
    max_high_risk_pending: int = 0,
    require_graph: bool = False,
    fail_on_latest_failure: bool = False,
    strict: bool = False,
    redaction: str = "standard",
) -> dict[str, Any]:
    if strict:
        max_pending = 0 if max_pending is None else max_pending
        require_graph = True
        fail_on_latest_failure = True
    bundle = build_project_audit_bundle(
        config,
        store,
        limit_runs=limit_runs,
        rebuild_graph=rebuild_graph,
        redaction="none",
    )
    proposal_chain = bundle.get("proposal_chain") if isinstance(bundle.get("proposal_chain"), dict) else {}
    run_chain = bundle.get("run_chain") if isinstance(bundle.get("run_chain"), dict) else {}
    graph_status = ((bundle.get("graph_chain") or {}).get("status") or {}) if isinstance(bundle.get("graph_chain"), dict) else {}
    constitution = _constitution_status(config)
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not constitution["ok"]:
        failures.append(
            {
                "code": "constitution_incomplete",
                "message": "Experience constitution is missing or incomplete.",
                "details": constitution,
            }
        )
    pending_count = int(proposal_chain.get("pending_count") or 0)
    high_risk_pending = int(proposal_chain.get("high_risk_pending_count") or 0)
    if high_risk_pending > max_high_risk_pending:
        failures.append(
            {
                "code": "high_risk_pending_proposals",
                "message": "High-risk or p0 proposals require review before release.",
                "count": high_risk_pending,
                "allowed": max_high_risk_pending,
            }
        )
    if max_pending is not None and pending_count > max(0, int(max_pending)):
        failures.append(
            {
                "code": "pending_proposals_over_limit",
                "message": "Pending proposals exceed the configured audit gate.",
                "count": pending_count,
                "allowed": max(0, int(max_pending)),
            }
        )
    elif pending_count:
        warnings.append(
            {
                "code": "pending_proposals",
                "message": "Pending low/medium-risk proposals still need human review before they become active.",
                "count": pending_count,
            }
        )

    graph_nodes = int(graph_status.get("nodes") or 0)
    graph_edges = int(graph_status.get("edges") or 0)
    if require_graph and graph_nodes <= 0:
        failures.append(
            {
                "code": "graph_missing",
                "message": "Experience graph is required but has no nodes.",
                "recommended_command": "praxile audit check --rebuild-graph --require-graph",
            }
        )
    elif graph_nodes <= 0:
        warnings.append(
            {
                "code": "graph_not_built",
                "message": "Experience graph has no nodes; rebuild it for stronger lineage evidence.",
                "recommended_command": "praxile graph status --rebuild",
            }
        )
    elif graph_edges <= 0:
        warnings.append(
            {
                "code": "graph_has_no_edges",
                "message": "Experience graph exists but has no relationship edges yet.",
            }
        )

    runs = run_chain.get("runs") or []
    if not runs:
        warnings.append({"code": "no_recent_runs", "message": "No recent trajectory rows were included in the audit bundle."})
    else:
        latest = runs[0] if isinstance(runs[0], dict) else {}
        if fail_on_latest_failure and latest.get("status") in {"failed", "needs_human"}:
            failures.append(
                {
                    "code": "latest_run_not_completed",
                    "message": "Latest run did not complete cleanly.",
                    "task_id": latest.get("task_id"),
                    "status": latest.get("status"),
                }
            )

    passed = not failures
    report = _base_report(config, "check", "project") | {
        "found": True,
        "check": {
            "passed": passed,
            "exit_code": 0 if passed else 1,
            "failures": failures,
            "warnings": warnings,
            "policy": {
                "max_pending": max_pending,
                "max_high_risk_pending": max_high_risk_pending,
                "require_graph": require_graph,
                "fail_on_latest_failure": fail_on_latest_failure,
                "strict": strict,
            },
        },
        "bundle_summary": {
            "run_count": run_chain.get("count", 0),
            "proposal_count": proposal_chain.get("count", 0),
            "pending_count": pending_count,
            "high_risk_pending_count": high_risk_pending,
            "asset_count": (bundle.get("asset_chain") or {}).get("count", 0) if isinstance(bundle.get("asset_chain"), dict) else 0,
            "graph_nodes": graph_nodes,
            "graph_edges": graph_edges,
            "constitution": constitution,
        },
        "governance_summary": bundle.get("governance_summary") or {},
    }
    return _finalize_report(report, redaction=redaction)


def format_audit_report(report: dict[str, Any]) -> str:
    lines = [
        f"Praxile audit: {report.get('audit_type')} {report.get('subject_id')}",
        f"- generated_at: {report.get('generated_at')}",
        f"- project: {report.get('project_root')}",
    ]
    if not report.get("found"):
        lines.append(f"- not found: {report.get('subject_id')}")
        return "\n".join(lines)
    subject = report.get("subject") if isinstance(report.get("subject"), dict) else {}
    for key in ["task_id", "proposal_id", "path", "type", "status", "title", "summary"]:
        if subject.get(key) is not None:
            lines.append(f"- {key}: {shorten(str(subject.get(key)), 180)}")
    if report.get("source_chain"):
        source = report["source_chain"]
        if isinstance(source, dict):
            if source.get("source_task_id"):
                lines.append(f"- source_task_id: {source.get('source_task_id')}")
            if source.get("loaded_assets"):
                lines.append(f"- loaded_assets: {len(source.get('loaded_assets') or [])}")
    if report.get("decision_chain"):
        chain = report["decision_chain"]
        if isinstance(chain, dict):
            lines.append(f"- actions: {len(chain.get('actions') or [])}")
            executor = chain.get("executor_attribution") or {}
            if executor:
                lines.append(f"- executor_attribution: {executor.get('quality', 'unknown')}")
    if report.get("proposal_chain"):
        proposals = report["proposal_chain"]
        if isinstance(proposals, dict):
            count = proposals.get("proposal_count", proposals.get("related_proposal_count", 0))
            if not count and proposals.get("count") is not None:
                count = proposals.get("count")
            lines.append(f"- proposals: {count}")
            if proposals.get("pending_count") is not None:
                lines.append(f"- pending_proposals: {proposals.get('pending_count')}")
    if report.get("asset_chain"):
        assets = report["asset_chain"]
        if isinstance(assets, dict):
            lines.append(f"- assets: {assets.get('count')}")
    if report.get("reflect_chain"):
        reflect = report["reflect_chain"]
        if isinstance(reflect, dict) and reflect.get("included"):
            lines.append(f"- reflect_reports: {reflect.get('count', 0)}")
    if report.get("governance_summary"):
        summary = report["governance_summary"]
        if isinstance(summary, dict):
            lines.append(f"- ready_for_release_review: {summary.get('ready_for_release_review')}")
            for action in summary.get("recommended_actions") or []:
                lines.append(f"  - {action}")
    if report.get("check"):
        check = report["check"]
        if isinstance(check, dict):
            lines.append(f"- check_passed: {check.get('passed')}")
            for failure in check.get("failures") or []:
                if isinstance(failure, dict):
                    lines.append(f"  failure[{failure.get('code')}]: {failure.get('message')}")
            for warning in check.get("warnings") or []:
                if isinstance(warning, dict):
                    lines.append(f"  warning[{warning.get('code')}]: {warning.get('message')}")
    graph = report.get("graph") if isinstance(report.get("graph"), dict) else {}
    if graph:
        lines.append(f"- graph_found: {graph.get('found')}")
        lines.append(f"- graph_edges: {len(graph.get('edges') or [])}")
    lines.append("")
    lines.append("Use `--json` or `--output <PATH>` for the full audit export.")
    return "\n".join(lines)


def normalize_audit_asset_path(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"


def _base_report(config: Config, audit_type: str, subject_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "audit_type": audit_type,
        "subject_id": subject_id,
        "generated_at": utc_now(),
        "project_root": str(config.paths.root),
        "state_root": str(config.paths.state),
    }


def _not_found_report(config: Config, audit_type: str, subject_id: str) -> dict[str, Any]:
    return _base_report(config, audit_type, subject_id) | {"found": False}


def _finalize_report(report: dict[str, Any], *, redaction: str) -> dict[str, Any]:
    profile = _normalize_redaction_profile(redaction)
    stats = {
        "profile": profile,
        "applied": profile != "none",
        "best_effort": profile != "none",
        "redacted_value_count": 0,
        "strict_excerpt_redaction_count": 0,
    }
    if profile == "none":
        finalized = dict(report)
    else:
        finalized = _redact_value(report, profile=profile, stats=stats, key_path=())
    finalized["redaction"] = stats
    bundle = finalized.get("bundle") if isinstance(finalized.get("bundle"), dict) else None
    if bundle is not None:
        bundle["raw_secret_values"] = profile == "none"
        bundle["redaction_profile"] = profile
    return finalized


def _normalize_redaction_profile(profile: str | None) -> str:
    value = str(profile or "standard").strip().lower()
    if value not in {"standard", "strict", "none"}:
        return "standard"
    return value


SENSITIVE_VALUE_KEYWORDS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "access_token",
    "refresh_token",
}
STRICT_EXCERPT_KEYS = {
    "content_excerpt",
    "observation_excerpt",
    "output_excerpt",
    "diff_excerpt",
}
SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{12,})\b"),
    re.compile(r"\b(AKIA[0-9A-Z]{12,})\b"),
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{12,})\b"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTHORIZATION)[A-Z0-9_]*)"
        r"(\s*[:=]\s*['\"]?)([^'\"\s,;}]{4,})"
    ),
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password|credential|authorization)\b"
        r"(\s*[:=]\s*['\"]?)([^'\"\s,;}]{4,})"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]


def _redact_value(value: Any, *, profile: str, stats: dict[str, Any], key_path: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = _redact_value(item, profile=profile, stats=stats, key_path=(*key_path, key_text))
        return result
    if isinstance(value, list):
        return [_redact_value(item, profile=profile, stats=stats, key_path=key_path) for item in value]
    if isinstance(value, str):
        key = key_path[-1].lower() if key_path else ""
        if profile == "strict" and key in STRICT_EXCERPT_KEYS and value:
            stats["strict_excerpt_redaction_count"] += 1
            return f"[redacted:strict:{key}]"
        if _sensitive_value_key(key):
            stats["redacted_value_count"] += 1
            return "[REDACTED]"
        return _redact_string(value, stats)
    return value


def _sensitive_value_key(key: str) -> bool:
    if not key:
        return False
    if key.endswith("_env") or key in {"token_env", "api_key_env"}:
        return False
    normalized = key.replace("-", "_")
    if normalized in SENSITIVE_VALUE_KEYWORDS:
        return True
    parts = set(normalized.split("_"))
    if parts & {"auth", "authorization", "token", "password", "passwd", "secret", "credential"}:
        return True
    return any(normalized.endswith(f"_{keyword}") for keyword in SENSITIVE_VALUE_KEYWORDS)


def _redact_string(value: str, stats: dict[str, Any]) -> str:
    redacted = value
    for pattern in SECRET_VALUE_PATTERNS:
        before = redacted
        if pattern.groups >= 3:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted)
        elif pattern.groups >= 1:
            redacted = pattern.sub("[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
        if redacted != before:
            stats["redacted_value_count"] += 1
    return redacted


def _constitution_status(config: Config) -> dict[str, Any]:
    path = config.paths.state / "constitution.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    required = [
        "No durable asset without evidence",
        "No global rule from a single run",
        "No memory update without scope and anti-scope",
        "No proposal accepted without source task and rollback path",
    ]
    missing = [item for item in required if item not in text]
    return {
        "ok": path.exists() and not missing,
        "path": str(path),
        "exists": path.exists(),
        "missing": missing,
    }


def _root_relative(config: Config, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve(strict=False).relative_to(config.paths.root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _graph_report(store: ExperienceStore, ref: str) -> dict[str, Any]:
    try:
        return store.graph_explain(ref, depth=2, limit=120)
    except Exception as exc:
        return {"found": False, "ref": ref, "error": str(exc)}


def _proposals_for_run(store: ExperienceStore, task_id: str, trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in trajectory.get("experience_candidates") or []:
        if isinstance(item, dict) and item.get("proposal_id"):
            proposal = store.find_proposal(str(item.get("proposal_id"))) or item
            by_id[str(item["proposal_id"])] = proposal
    for proposal in store.list_proposals(status=None, limit=10000):
        source_task_id = str(proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id") or "")
        if source_task_id == task_id and proposal.get("proposal_id"):
            by_id[str(proposal["proposal_id"])] = proposal
    return sorted(by_id.values(), key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def _proposals_for_asset(store: ExperienceStore, asset_path: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for proposal in store.list_proposals(status=None, limit=10000):
        targets = {_normalize_target_path(value) for value in proposal.get("target_files") or []}
        for change in proposal.get("changes") or []:
            if isinstance(change, dict) and change.get("path"):
                targets.add(_normalize_target_path(str(change["path"])))
        if asset_path in targets:
            matches.append(proposal)
    return matches


def _proposal_target_assets(store: ExperienceStore, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    targets = []
    seen: set[str] = set()
    for target in proposal.get("target_files") or []:
        normalized = _normalize_target_path(str(target))
        if normalized in seen:
            continue
        seen.add(normalized)
        asset = store.get_asset(normalized)
        if asset:
            targets.append(_compact_asset(asset))
        else:
            targets.append({"path": normalized, "indexed": False})
    return targets


def _normalize_target_path(path: str) -> str:
    return normalize_audit_asset_path(path)


def _compact_run(trajectory: dict[str, Any] | None) -> dict[str, Any] | None:
    if not trajectory:
        return None
    report = trajectory.get("reward_report") if isinstance(trajectory.get("reward_report"), dict) else {}
    return {
        "task_id": trajectory.get("task_id"),
        "user_task": trajectory.get("user_task"),
        "status": (trajectory.get("result") or {}).get("status"),
        "summary": (trajectory.get("result") or {}).get("summary"),
        "reward_overall": report.get("overall"),
        "started_at": trajectory.get("start_time"),
        "ended_at": trajectory.get("end_time"),
    }


def _compact_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": row.get("task_id"),
        "user_task": row.get("user_task"),
        "status": row.get("status"),
        "reward_score": row.get("reward_score"),
        "trajectory_path": row.get("trajectory_path"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "status": proposal.get("status"),
        "risk_level": proposal.get("risk_level"),
        "priority": proposal.get("priority"),
        "confidence": proposal.get("confidence"),
        "confidence_level": proposal.get("confidence_level"),
        "title": proposal.get("title"),
        "source_task_id": proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id"),
        "target_files": proposal.get("target_files") or [],
        "evidence_summary": proposal.get("evidence_summary"),
    }


def _compact_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": asset.get("path"),
        "type": asset.get("type"),
        "title": asset.get("title"),
        "status": asset.get("status", "active"),
        "confidence": asset.get("confidence"),
        "usage_count": asset.get("usage_count", 0),
        "positive_outcome_count": asset.get("positive_outcome_count", 0),
        "negative_outcome_count": asset.get("negative_outcome_count", 0),
    }


def _compact_loaded_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.get("path"),
            "type": item.get("type") or item.get("kind"),
            "final_score": item.get("final_score", item.get("score")),
            "matched_terms": item.get("matched_terms") or [],
            "matched_fields": item.get("matched_fields") or [],
            "why_loaded": item.get("why_loaded") or item.get("reason"),
        }
        for item in assets
        if isinstance(item, dict)
    ]


def _compact_usage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.get("path"),
            "outcome": item.get("outcome"),
            "used_in_prompt": item.get("used_in_prompt"),
            "referenced": item.get("referenced"),
            "used_explicitly": item.get("used_explicitly"),
            "attribution_level": item.get("attribution_level"),
            "score": item.get("score"),
            "why_loaded": item.get("why_loaded"),
        }
        for item in rows
    ]


def _project_assets(store: ExperienceStore) -> list[dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for kind in ["rule", "skill", "eval", "memory", "failure", "pattern", "trajectory"]:
        for item in store.list_assets(kind, include_inactive=True):
            path = str(item.get("path") or "")
            if path:
                assets[path] = item
    return sorted(assets.values(), key=lambda item: str(item.get("path") or ""))


def _latest_reflect_reports(config: Config, *, limit: int = 5) -> list[dict[str, Any]]:
    root = config.paths.state / "experience" / "reflect"
    if not root.exists():
        return []
    paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    reports: list[dict[str, Any]] = []
    safe_limit = max(0, int(5 if limit is None else limit))
    for path in paths[:safe_limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reports.append({"path": _root_relative(config, path), "read_error": str(exc)})
            continue
        reports.append(_compact_reflect_report(config, path, data))
    return reports


def _compact_reflect_report(config: Config, path: Path, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "path": _root_relative(config, path),
        "reflect_id": report.get("reflect_id"),
        "created_at": report.get("created_at"),
        "scope": report.get("scope") or {},
        "finding_counts": summary.get("finding_counts") or {},
        "proposal_counts": summary.get("proposal_counts") or {},
        "written_proposal_paths": report.get("written_proposal_paths") or [],
        "no_assets_modified": report.get("no_assets_modified", True),
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _bundle_recommended_actions(
    pending: list[dict[str, Any]],
    high_risk_pending: list[dict[str, Any]],
    graph_status: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if high_risk_pending:
        actions.append("Review high-risk or p0 proposals before treating the project experience base as release-ready.")
    elif pending:
        actions.append("Review pending proposals; accept only evidence-backed project-local updates.")
    else:
        actions.append("No pending proposal review is required before release from Praxile's local governance view.")
    if not graph_status.get("nodes"):
        actions.append("Run `praxile graph status --rebuild` or `praxile audit bundle --rebuild-graph` for relationship evidence.")
    return actions


def _compact_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for action in actions:
        executor = action.get("executor") if isinstance(action.get("executor"), dict) else {}
        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        item = {
            "step": action.get("step"),
            "action_type": action.get("action_type"),
            "status": action.get("status"),
            "executor_id": executor.get("executor_id"),
            "executor_kind": executor.get("kind"),
            "input": _safe_action_input(action.get("input") or {}),
            "observation_excerpt": shorten(str(observation.get("output") or ""), 500),
            "risk_level": action.get("risk_level") or observation.get("risk_level"),
        }
        compact.append(item)
    return compact


def _safe_action_input(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ["path", "command", "query", "pattern", "url", "actions"]:
        if key not in value:
            continue
        if key == "actions" and isinstance(value[key], list):
            result[key] = [
                {"type": item.get("type"), "path": item.get("path"), "query": item.get("query")}
                for item in value[key]
                if isinstance(item, dict)
            ][:12]
        else:
            result[key] = value[key]
    return result


def _compact_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.get("path"),
            "operation": item.get("operation", "write"),
            "content_hash": _content_hash(item.get("content")),
            "content_excerpt": shorten(str(item.get("content") or ""), 500),
            "metadata": item.get("metadata") or {},
        }
        for item in changes
        if isinstance(item, dict)
    ]


def _content_hash(value: Any) -> str | None:
    if value is None:
        return None
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]


def audit_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
