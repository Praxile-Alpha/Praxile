from __future__ import annotations

from typing import Any
import json

from ..config import Config
from ..runtime import AgentRuntime
from ..store import ExperienceStore


def normalize_deliverable_contract(contract: Any) -> dict | None:
    """Validate and detach public caller requirements, never read a host path."""
    if contract is None:
        return None
    from ..deliverables import validate_contract
    validate_contract(contract)
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536:
        raise ValueError("deliverable contract exceeds 64 KiB")
    return json.loads(encoded)


class RunService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run(self, task: str, **kwargs: Any) -> dict[str, Any]:
        if "deliverable_contract" in kwargs:
            kwargs["deliverable_contract"] = normalize_deliverable_contract(kwargs["deliverable_contract"])
        return AgentRuntime(self.config).run(task, **kwargs)

    def latest(self) -> dict[str, Any] | None:
        return self.store.latest_trajectory()

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_trajectory(task_id)
