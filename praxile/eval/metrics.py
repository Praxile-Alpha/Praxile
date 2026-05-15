from __future__ import annotations

import json
from typing import Any


def score_metric(metric: str, proposals: list[dict[str, Any]], expected: dict[str, Any]) -> dict[str, Any]:
    if metric == "proposal_generated":
        passed = bool(proposals)
        return _result(metric, 1.0 if passed else 0.0, passed, f"{len(proposals)} proposal(s) generated")
    if metric == "proposal_type_match":
        expected_types = _expected_types(expected)
        actual = {str(item.get("type") or "") for item in proposals}
        hits = sorted(expected_types & actual)
        passed = bool(hits)
        return _result(metric, 1.0 if passed else 0.0, passed, f"expected={sorted(expected_types)} actual={sorted(actual)} hits={hits}")
    if metric == "keyword_hit":
        keywords = [str(item).lower() for item in expected.get("keywords", []) if str(item).strip()]
        haystack = _proposal_text(proposals).lower()
        hits = [keyword for keyword in keywords if keyword in haystack]
        score = (len(hits) / len(keywords)) if keywords else 1.0
        return _result(metric, round(score, 4), score >= 1.0, f"hits={hits} expected={keywords}")
    if metric == "min_proposals":
        minimum = int(expected.get("min_proposals") or 1)
        passed = len(proposals) >= minimum
        return _result(metric, 1.0 if passed else 0.0, passed, f"generated={len(proposals)} minimum={minimum}")
    return _result(metric, 0.0, False, "unknown metric")


def _result(metric: str, score: float, passed: bool, details: str) -> dict[str, Any]:
    return {"metric": metric, "score": score, "passed": passed, "details": details}


def _expected_types(expected: dict[str, Any]) -> set[str]:
    raw = expected.get("proposal_types", expected.get("proposal_type"))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(item) for item in raw}
    return set()


def _proposal_text(proposals: list[dict[str, Any]]) -> str:
    return json.dumps(proposals, ensure_ascii=False, sort_keys=True)
