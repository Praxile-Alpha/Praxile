from __future__ import annotations

from typing import Any

from ..config import Config
from ..runtime import AgentRuntime
from ..store import ExperienceStore


class RunService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run(self, task: str, **kwargs: Any) -> dict[str, Any]:
        return AgentRuntime(self.config).run(task, **kwargs)

    def latest(self) -> dict[str, Any] | None:
        return self.store.latest_trajectory()

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_trajectory(task_id)
