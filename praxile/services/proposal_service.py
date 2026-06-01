from __future__ import annotations

import copy
import json
from typing import Any

from ..store import ExperienceStore
from ..utils import utc_now
from .errors import ServiceError


class ProposalService:
    def __init__(self, store: ExperienceStore):
        self.store = store

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        proposal_type: str | None = None,
        risk: str | None = None,
        confidence: str | None = None,
        source_run: str | None = None,
        recommended: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.store.list_proposals(status=status, limit=max(limit * 4, limit))
        filtered = [
            item
            for item in rows
            if self._matches(item, proposal_type=proposal_type, risk=risk, confidence=confidence, source_run=source_run, recommended=recommended, query=query)
        ][:limit]
        return [compact_proposal(item) for item in filtered]

    def detail(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.store.find_proposal(proposal_id)
        if not proposal:
            raise ServiceError(404, "Proposal not found")
        return {**proposal, "review_explanation": self.explain(proposal)}

    def explain(self, proposal: dict[str, Any]) -> dict[str, Any]:
        source_task = proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id")
        evidence = proposal.get("evidence_items") or proposal.get("evidence") or []
        changes = proposal.get("changes") or []
        target_files = proposal.get("target_files") or [change.get("path") for change in changes if isinstance(change, dict)]
        recommended = proposal.get("recommended_action_override") or (proposal.get("review_recommendation") or {}).get("recommended_action")
        reasons = []
        if proposal.get("risk_level"):
            reasons.append(f"risk={proposal.get('risk_level')}")
        if proposal.get("confidence") is not None:
            reasons.append(f"confidence={proposal.get('confidence')}")
        if evidence:
            reasons.append(f"evidence_items={len(evidence)}")
        if target_files:
            reasons.append(f"target_files={len(target_files)}")
        return {
            "source_task_id": source_task,
            "recommended_action": recommended or "inspect",
            "why_in_inbox": "; ".join(reasons) or "pending proposal awaiting human review",
            "target_files": target_files,
            "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
            "change_count": len(changes) if isinstance(changes, list) else 0,
            "filters": {
                "type": proposal.get("type"),
                "risk": proposal.get("risk_level"),
                "confidence_level": proposal.get("confidence_level"),
                "source_run": source_task,
                "recommended": recommended,
            },
        }

    def _matches(
        self,
        proposal: dict[str, Any],
        *,
        proposal_type: str | None,
        risk: str | None,
        confidence: str | None,
        source_run: str | None,
        recommended: str | None,
        query: str | None,
    ) -> bool:
        if proposal_type and str(proposal.get("type") or "") != proposal_type:
            return False
        if risk and str(proposal.get("risk_level") or "") != risk:
            return False
        if source_run:
            source = proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id")
            if str(source or "") != source_run:
                return False
        if confidence and str(proposal.get("confidence_level") or "") != confidence:
            return False
        action = proposal.get("recommended_action_override") or (proposal.get("review_recommendation") or {}).get("recommended_action")
        if recommended and str(action or "") != recommended:
            return False
        if query:
            evidence = proposal.get("evidence_items") or proposal.get("evidence") or []
            evidence_text = " ".join(str(item) for item in evidence if item)
            source = proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id")
            haystack = " ".join(
                str(value or "")
                for value in [
                    proposal.get("proposal_id"),
                    proposal.get("type"),
                    proposal.get("title"),
                    proposal.get("reason"),
                    proposal.get("evidence_summary"),
                    evidence_text,
                    source,
                    action,
                    *(proposal.get("target_files") or []),
                ]
            ).lower()
            if query.lower() not in haystack:
                return False
        return True

    def edit(self, proposal_id: str, payload: dict[str, Any], *, edited_by: str = "web_console") -> dict[str, Any]:
        if not payload.get("confirm"):
            raise ServiceError(400, "`confirm` is required to edit a proposal")
        pending = self.store.find_proposal(proposal_id, status="pending")
        if not pending:
            raise ServiceError(404, "No pending proposal found")
        edited = self._edited_payload(proposal_id, pending, payload)
        events = pending.get("user_edits") if isinstance(pending.get("user_edits"), list) else []
        edited["user_edits"] = [
            *events,
            {
                "edited_at": utc_now(),
                "edited_by": edited_by,
                "reason": str(payload.get("reason") or "manual proposal edit").strip(),
            },
        ]
        self.store.write_proposal(edited)
        return self.store.find_proposal(proposal_id, status="pending") or edited

    def accept(self, proposal_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ServiceError(400, "`confirm` is required to accept a proposal")
        pending = self.store.find_proposal(proposal_id, status="pending")
        if not pending:
            raise ServiceError(404, "No pending proposal found")
        return self.store.apply_proposal(pending)

    def reject(self, proposal_id: str, *, reason: str | None = None) -> dict[str, Any]:
        reason = str(reason or "").strip()
        if not reason:
            raise ServiceError(400, "`reason` is required to reject a proposal")
        pending = self.store.find_proposal(proposal_id, status="pending")
        if not pending:
            raise ServiceError(404, "No pending proposal found")
        return self.store.reject_proposal(pending, reason=reason)

    def _edited_payload(self, proposal_id: str, pending: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload.get("proposal")
        if isinstance(raw, str):
            try:
                edited = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ServiceError(400, f"Invalid proposal JSON: {exc}") from exc
        elif isinstance(raw, dict):
            edited = copy.deepcopy(raw)
        else:
            edited = copy.deepcopy(pending)
            for key in ["title", "reason", "evidence", "changes", "risk_level", "confidence", "target_files"]:
                if key in payload:
                    edited[key] = payload[key]
        if not isinstance(edited, dict):
            raise ServiceError(400, "`proposal` must be an object")
        if edited.get("proposal_id") not in {None, proposal_id, pending.get("proposal_id")}:
            raise ServiceError(400, "Edited proposal_id must match the pending proposal")
        if edited.get("status") not in {None, "pending"}:
            raise ServiceError(400, "Only pending proposals can be edited through the web console")
        changes = edited.get("changes")
        if changes is not None and not isinstance(changes, list):
            raise ServiceError(400, "`changes` must be a list")
        edited["proposal_id"] = pending["proposal_id"]
        edited["status"] = "pending"
        for key in ["created_at", "source_task_id", "generated_by"]:
            if pending.get(key) is not None:
                edited[key] = pending[key]
        if not edited.get("target_files") and isinstance(changes, list):
            edited["target_files"] = [str(change.get("path")) for change in changes if isinstance(change, dict) and change.get("path")]
        return edited


def compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    service_like = ProposalService.__new__(ProposalService)
    service_like.store = None
    explanation = ProposalService.explain(service_like, proposal)
    return {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "title": proposal.get("title"),
        "status": proposal.get("status"),
        "risk_level": proposal.get("risk_level"),
        "confidence": proposal.get("confidence"),
        "confidence_level": proposal.get("confidence_level"),
        "recommended_action": proposal.get("recommended_action_override")
        or (proposal.get("review_recommendation") or {}).get("recommended_action"),
        "source_task_id": proposal.get("source_task_id") or (proposal.get("source") or {}).get("task_id"),
        "generated_by": proposal.get("generated_by"),
        "target_files": proposal.get("target_files") or [],
        "affected_assets": proposal.get("affected_assets") or proposal.get("affected_files") or [],
        "evidence_summary": proposal.get("evidence_summary"),
        "proposal_gate": proposal.get("proposal_gate") or {},
        "review_explanation": explanation,
        "created_at": proposal.get("created_at"),
        "updated_at": proposal.get("updated_at"),
    }
