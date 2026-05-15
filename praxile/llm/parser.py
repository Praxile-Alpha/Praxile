from __future__ import annotations

from typing import Any

from ..json_utils import RobustJSONError, parse_json_value


class LLMProposalParseError(ValueError):
    pass


def parse_proposal_response(response: str) -> list[dict[str, Any]]:
    try:
        parsed = parse_json_value(response)
    except RobustJSONError as exc:
        raise LLMProposalParseError(str(exc)) from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("proposals"), list):
        raw_items = parsed["proposals"]
    elif isinstance(parsed, list):
        raw_items = parsed
    else:
        raise LLMProposalParseError("LLM response must be a list or an object with a proposals list.")
    proposals: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, 1):
        if not isinstance(item, dict):
            raise LLMProposalParseError(f"proposal #{index} must be an object")
        _validate_required_fields(item, index=index)
        proposals.append(item)
    return proposals


def _validate_required_fields(item: dict[str, Any], *, index: int) -> None:
    required = ["title", "type", "confidence"]
    for key in required:
        if item.get(key) in {None, ""}:
            raise LLMProposalParseError(f"proposal #{index} missing required field `{key}`")
    if not (item.get("reason") or item.get("rationale")):
        raise LLMProposalParseError(f"proposal #{index} missing required field `reason` or `rationale`")
    if "content" not in item and not item.get("changes"):
        raise LLMProposalParseError(f"proposal #{index} missing `content` or `changes`")
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise LLMProposalParseError(f"proposal #{index} confidence must be numeric") from exc
    if not 0 <= confidence <= 1:
        raise LLMProposalParseError(f"proposal #{index} confidence must be between 0 and 1")
