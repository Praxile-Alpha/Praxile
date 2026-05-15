from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils import read_json


@dataclass(frozen=True)
class EvalCase:
    name: str
    input: dict[str, Any]
    expected: dict[str, Any]
    metrics: list[str]


@dataclass(frozen=True)
class EvalSuite:
    name: str
    path: Path
    cases: list[EvalCase]

    @classmethod
    def load(cls, path: Path) -> "EvalSuite":
        payload = read_json(path, {})
        if isinstance(payload, list):
            payload = {"name": path.stem, "cases": payload}
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: expected JSON object or list")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ValueError(f"{path}: expected non-empty `cases` list")
        cases: list[EvalCase] = []
        for index, raw_case in enumerate(raw_cases, 1):
            if not isinstance(raw_case, dict):
                raise ValueError(f"{path}: case #{index} must be an object")
            expected = raw_case.get("expected") if isinstance(raw_case.get("expected"), dict) else {}
            metrics = raw_case.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                metrics = _default_metrics(expected)
            cases.append(
                EvalCase(
                    name=str(raw_case.get("name") or f"case-{index}"),
                    input=raw_case.get("input") if isinstance(raw_case.get("input"), dict) else {},
                    expected=expected,
                    metrics=[str(item) for item in metrics],
                )
            )
        return cls(name=str(payload.get("name") or path.stem), path=path.resolve(), cases=cases)


def _default_metrics(expected: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    if expected.get("proposal_type") or expected.get("proposal_types"):
        metrics.append("proposal_type_match")
    if expected.get("keywords"):
        metrics.append("keyword_hit")
    if expected.get("min_proposals") is not None:
        metrics.append("min_proposals")
    return metrics or ["proposal_generated"]
