"""Versioned evidence records for explicit runtime subtask boundaries."""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SubtaskEpisode:
    episode_id: str
    task_id: str
    goal: str
    state_before: dict[str, Any]
    started_at: str
    state_after: dict[str, Any] | None = None
    ended_at: str | None = None
    status: str = "running"
    claimed_outcome: str | None = None
    summary: str = ""
    action_refs: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    observed_status_counts: dict[str, int] = field(default_factory=dict)
    uncertainty: list[str] = field(default_factory=lambda: ["Model-defined boundary; causal attribution and outcome are not independently verified."])
    schema_version: str = "praxile.subtask_episode.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
