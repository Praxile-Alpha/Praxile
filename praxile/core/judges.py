from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JudgeResult:
    score: float
    passed: bool = True
    details: dict[str, Any] = field(default_factory=dict)


class BaseJudge(ABC):
    name = "base"

    @abstractmethod
    def evaluate(self, *args: Any, **kwargs: Any) -> dict[str, Any] | JudgeResult:
        ...
