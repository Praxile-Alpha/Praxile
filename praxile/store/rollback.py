from __future__ import annotations

from typing import Any

from .base import StoreRepository


class RollbackRepository(StoreRepository):
    def rollback_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.store.rollback_proposal(proposal_id)
