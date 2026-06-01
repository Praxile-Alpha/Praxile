from __future__ import annotations

from typing import Any

from ..config import Config
from ..reflect import ReflectEngine, ReflectScope
from ..store import ExperienceStore


class ReflectService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run(self, scope: ReflectScope | None = None, *, write_proposals: bool = False) -> dict[str, Any]:
        return ReflectEngine(self.config, self.store).run(scope or ReflectScope(), write_proposals=write_proposals)
