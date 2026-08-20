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
    set_type: str = "regression"
    owner: str = "project"
    expected_owner: str = "project"


@dataclass(frozen=True)
class EvalSuite:
    name: str
    path: Path
    cases: list[EvalCase]
    owner: str = "project"
    expected_owner: str = "project"

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
        owner = str(payload.get("owner") or "project")
        expected_owner = str(payload.get("expected_owner") or owner)
        default_set = str(payload.get("set_type") or "regression")
        cases: list[EvalCase] = []
        for index, raw_case in enumerate(raw_cases, 1):
            if not isinstance(raw_case, dict):
                raise ValueError(f"{path}: case #{index} must be an object")
            expected = raw_case.get("expected") if isinstance(raw_case.get("expected"), dict) else {}
            metrics = raw_case.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                metrics = _default_metrics(expected)
            set_type = str(raw_case.get("set_type") or default_set)
            if set_type not in {"source", "regression", "sealed"}:
                raise ValueError(f"{path}: case #{index} set_type must be source, regression, or sealed")
            case_owner = str(raw_case.get("owner") or owner)
            case_expected_owner = str(raw_case.get("expected_owner") or expected_owner)
            if set_type == "sealed" and case_expected_owner == "proposal_composer":
                raise ValueError(f"{path}: sealed expected outputs cannot be owned by proposal_composer")
            cases.append(
                EvalCase(
                    name=str(raw_case.get("name") or f"case-{index}"),
                    input=raw_case.get("input") if isinstance(raw_case.get("input"), dict) else {},
                    expected=expected,
                    metrics=[str(item) for item in metrics],
                    set_type=set_type,
                    owner=case_owner,
                    expected_owner=case_expected_owner,
                )
            )
        return cls(
            name=str(payload.get("name") or path.stem),
            path=path.resolve(),
            cases=cases,
            owner=owner,
            expected_owner=expected_owner,
        )


def _default_metrics(expected: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    if expected.get("proposal_type") or expected.get("proposal_types"):
        metrics.append("proposal_type_match")
    if expected.get("keywords"):
        metrics.append("keyword_hit")
    if expected.get("min_proposals") is not None:
        metrics.append("min_proposals")
    if expected.get("returncode") is not None:
        metrics.append("returncode_match")
    if expected.get("output_contains"):
        metrics.append("output_contains")
    return metrics or ["proposal_generated"]
