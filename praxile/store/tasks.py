from __future__ import annotations

from typing import Any

from .base import StoreRepository


class TasksRepository(StoreRepository):
    def list_history(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.store.list_history(**kwargs)
