from __future__ import annotations

import json
import re
from typing import Any


class RobustJSONError(ValueError):
    pass


def parse_json_object(text: str) -> dict[str, Any]:
    parsed = parse_json_value(text)
    if not isinstance(parsed, dict):
        raise RobustJSONError("Expected a JSON object.")
    return parsed


def parse_jsonc_object(text: str) -> dict[str, Any]:
    return parse_json_object(strip_json_comments(text))


def parse_json_value(text: str) -> Any:
    candidates = _json_candidates(text)
    errors: list[str] = []
    for candidate in candidates:
        repaired = _repair_trailing_commas(candidate.strip())
        try:
            return json.loads(repaired)
        except json.JSONDecodeError as exc:
            errors.append(f"{exc.msg} at {exc.pos}")
    detail = "; ".join(errors[:3]) if errors else "no JSON candidate found"
    raise RobustJSONError(f"Unable to parse JSON: {detail}")


def _json_candidates(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    candidates: list[str] = []
    fence = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(stripped)
    balanced = _extract_first_balanced_json(stripped)
    if balanced and balanced not in candidates:
        candidates.append(balanced)
    return candidates


def _extract_first_balanced_json(text: str) -> str | None:
    start = -1
    opening = ""
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            opening = char
            break
    if start < 0:
        return None
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    in_string = False
    escape = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append("}" if char == "{" else "]")
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def _repair_trailing_commas(text: str) -> str:
    # Remove commas before object/array close, preserving anything inside strings.
    result: list[str] = []
    in_string = False
    escape = False
    index = 0
    while index < len(text):
        char = text[index]
        if escape:
            result.append(char)
            escape = False
            index += 1
            continue
        if char == "\\" and in_string:
            result.append(char)
            escape = True
            index += 1
            continue
        if char == '"':
            in_string = not in_string
            result.append(char)
            index += 1
            continue
        if char == "," and not in_string:
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        result.append(char)
        index += 1
    return "".join(result)


def strip_json_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    escape = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if escape:
            result.append(char)
            escape = False
            index += 1
            continue
        if char == "\\" and in_string:
            result.append(char)
            escape = True
            index += 1
            continue
        if char == '"':
            in_string = not in_string
            result.append(char)
            index += 1
            continue
        if not in_string and char == "/" and next_char == "/":
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if not in_string and char == "/" and next_char == "*":
            index += 2
            while index + 1 < len(text) and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)
