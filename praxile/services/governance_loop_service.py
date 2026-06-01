from __future__ import annotations

import json
from typing import Any

from ..audit import build_project_audit_check
from ..config import Config
from ..reflect import ReflectEngine, ReflectScope
from ..store import ExperienceStore
from ..utils import new_id, utc_now, write_json
from .context_juice_service import ContextJuiceService
from .context_service import RepositoryContextService


class GovernanceLoopService:
    """Runs a safe, proposal-only governance pass for local repository context."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run_once(
        self,
        *,
        write: bool = True,
        compress: bool = False,
        rebuild_graph: bool | None = None,
        run_audit: bool | None = None,
        run_reflect: bool | None = None,
        write_reflect_proposals: bool = False,
    ) -> dict[str, Any]:
        report_id = new_id("gov")
        defaults = self.config.get("governance_loop", default={}) if isinstance(self.config.get("governance_loop", default={}), dict) else {}
        graph_enabled = bool(defaults.get("rebuild_graph", True)) if rebuild_graph is None else bool(rebuild_graph)
        audit_enabled = bool(defaults.get("run_audit", True)) if run_audit is None else bool(run_audit)
        reflect_enabled = bool(defaults.get("run_reflect", False)) if run_reflect is None else bool(run_reflect)
        compress_enabled = bool(defaults.get("compress_sync_report", False)) if compress is False else bool(compress)

        events: list[dict[str, Any]] = []
        started_at = utc_now()

        context_snapshot = RepositoryContextService(self.config, self.store).sync(write=write, refresh_map=True)
        events.append(self._event("sync", "completed", "Repository context snapshot captured.", context_snapshot.get("snapshot_id")))

        compression: dict[str, Any] | None = None
        if compress_enabled:
            text = json.dumps(context_snapshot, ensure_ascii=False, indent=2)
            compression = ContextJuiceService(self.config, self.store).compress_text(
                text,
                source_type="repo_snapshot",
                source_id=str(context_snapshot.get("snapshot_id") or report_id),
                role="evidence_extraction",
                write=write,
            )
            events.append(self._event("compress", "completed", "Context snapshot compressed for evidence extraction.", compression.get("compression_id")))

        graph: dict[str, Any] | None = None
        if graph_enabled:
            if write:
                graph = self.store.rebuild_experience_graph()
                events.append(self._event("graph", "completed", "Experience graph rebuilt.", None))
            else:
                graph = self.store.graph_status()
                events.append(self._event("graph", "dry_run", "Graph rebuild skipped because write=false.", None))

        audit: dict[str, Any] | None = None
        if audit_enabled:
            audit = build_project_audit_check(self.config, self.store, rebuild_graph=False, strict=False)
            audit_status = "passed" if audit.get("ok") else "needs_review"
            events.append(self._event("audit", audit_status, "Project audit check completed.", None))

        reflect: dict[str, Any] | None = None
        if reflect_enabled:
            if write:
                reflect = ReflectEngine(self.config, self.store).run(ReflectScope(), write_proposals=bool(write_reflect_proposals))
                proposal_count = len(reflect.get("generated_proposals") or []) if isinstance(reflect, dict) else 0
                events.append(self._event("reflect", "completed", f"Reflect run completed with {proposal_count} generated proposal candidates.", None))
            else:
                events.append(self._event("reflect", "dry_run", "Reflect skipped because write=false.", None))

        report = {
            "report_id": report_id,
            "kind": "background_governance_loop_report",
            "created_at": started_at,
            "completed_at": utc_now(),
            "safety_contract": {
                "auto_accept_proposals": False,
                "edit_code": False,
                "rewrite_durable_assets": False,
                "dangerous_shell": False,
            },
            "options": {
                "write": write,
                "compress": compress_enabled,
                "rebuild_graph": graph_enabled,
                "run_audit": audit_enabled,
                "run_reflect": reflect_enabled,
                "write_reflect_proposals": bool(write_reflect_proposals),
            },
            "events": events,
            "context_snapshot": self._compact_snapshot(context_snapshot),
            "compression": self._compact_compression(compression),
            "graph": graph,
            "audit": self._compact_audit(audit),
            "reflect": self._compact_reflect(reflect),
            "human_review_required": True,
        }
        if write:
            root = self.config.paths.state / "context" / "governance"
            write_json(root / f"{report_id}.json", report)
            write_json(root / "latest.json", report)
            report["path"] = (root / f"{report_id}.json").relative_to(self.config.paths.root).as_posix()
        return report

    def _event(self, step: str, status: str, message: str, artifact_id: str | None) -> dict[str, Any]:
        return {
            "step": step,
            "status": status,
            "message": message,
            "artifact_id": artifact_id,
            "created_at": utc_now(),
        }

    def _compact_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        status = snapshot.get("status") if isinstance(snapshot.get("status"), dict) else {}
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "path": snapshot.get("path"),
            "health": status.get("health"),
            "freshness": status.get("freshness"),
            "scanned_files": (status.get("repository") or {}).get("scanned_files") if isinstance(status.get("repository"), dict) else None,
        }

    def _compact_compression(self, compression: dict[str, Any] | None) -> dict[str, Any] | None:
        if not compression:
            return None
        return {
            "compression_id": compression.get("compression_id"),
            "role": compression.get("role"),
            "ratio": compression.get("ratio"),
            "estimated_savings": compression.get("estimated_savings"),
            "path": compression.get("path"),
            "markdown_path": compression.get("markdown_path"),
        }

    def _compact_audit(self, audit: dict[str, Any] | None) -> dict[str, Any] | None:
        if not audit:
            return None
        return {
            "ok": audit.get("ok"),
            "failure_count": len(audit.get("failures") or []),
            "warning_count": len(audit.get("warnings") or []),
            "failures": audit.get("failures") or [],
            "warnings": audit.get("warnings") or [],
        }

    def _compact_reflect(self, reflect: dict[str, Any] | None) -> dict[str, Any] | None:
        if not reflect:
            return None
        return {
            "reflect_id": reflect.get("reflect_id"),
            "finding_count": len(reflect.get("findings") or []),
            "generated_proposal_count": len(reflect.get("generated_proposals") or []),
        }
