from __future__ import annotations

from typing import Any

from ..audit import build_asset_audit, build_project_audit_bundle, build_project_audit_check, build_proposal_audit, build_run_audit
from ..config import Config
from ..store import ExperienceStore


class AuditService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run(self, run_id: str = "latest", **kwargs: Any) -> dict[str, Any]:
        return build_run_audit(self.config, self.store, run_id=run_id, **kwargs)

    def proposal(self, proposal_id: str, **kwargs: Any) -> dict[str, Any]:
        return build_proposal_audit(self.config, self.store, proposal_id=proposal_id, **kwargs)

    def asset(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return build_asset_audit(self.config, self.store, asset_path=path, **kwargs)

    def bundle(self, **kwargs: Any) -> dict[str, Any]:
        return build_project_audit_bundle(self.config, self.store, **kwargs)

    def check(self, **kwargs: Any) -> dict[str, Any]:
        return build_project_audit_check(self.config, self.store, **kwargs)
