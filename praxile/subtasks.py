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
    failure_evidence: list[dict[str, Any]] = field(default_factory=list)
    failure_attribution: dict[str, Any] = field(default_factory=lambda: {
        "status": "not_assessed", "cause": None,
        "reason": "Observed failures do not independently establish a root cause."})
    uncertainty: list[str] = field(default_factory=lambda: ["Model-defined boundary; causal attribution and outcome are not independently verified."])
    schema_version: str = "praxile.subtask_episode.v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
