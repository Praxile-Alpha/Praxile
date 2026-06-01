from __future__ import annotations

from typing import Any

from ..store import ExperienceStore


class GraphService:
    def __init__(self, store: ExperienceStore):
        self.store = store

    def status(self) -> dict[str, Any]:
        return self.store.graph_status()

    def rebuild(self) -> dict[str, Any]:
        return self.store.rebuild_experience_graph()

    def explain(self, ref: str, *, depth: int = 2, limit: int = 100) -> dict[str, Any]:
        return self.store.graph_explain(ref, depth=depth, limit=limit)
