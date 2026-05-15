from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AgentAdapter(ABC):
    """Boundary for optional external agent trace/proposal formats."""

    name = "base"

    @abstractmethod
    def to_trajectory(self, agent_output: Any) -> dict[str, Any]:
        """Convert an external agent output into a Praxile trajectory dict."""

    @abstractmethod
    def from_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        """Convert a Praxile proposal into an external-safe payload."""
