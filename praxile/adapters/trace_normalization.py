from __future__ import annotations

from typing import Any, Mapping


DEFAULT_EVENT_STRING_LIMIT = 20_000
DEFAULT_RAW_OUTPUT_LIMIT = 4_000


def bounded_native_message(
    message: Mapping[str, Any],
    *,
    string_limit: int = DEFAULT_EVENT_STRING_LIMIT,
    raw_output_limit: int = DEFAULT_RAW_OUTPUT_LIMIT,
) -> dict[str, Any]:
    """Bound adapter event payloads while native artifacts retain full fidelity."""

    normalized = _bounded(dict(message), string_limit=max(256, int(string_limit)))
    if not isinstance(normalized, dict):
        return {"value": normalized}
    extra = normalized.get("extra")
    original_extra = message.get("extra")
    if isinstance(extra, dict) and isinstance(original_extra, Mapping):
        raw_output = original_extra.get("raw_output")
        if isinstance(raw_output, str):
            extra.pop("raw_output", None)
            extra["raw_output_meta"] = _preview_meta(raw_output, max(256, int(raw_output_limit)))
    return normalized


def _bounded(value: Any, *, string_limit: int) -> Any:
    if isinstance(value, str):
        return _preview(value, string_limit)
    if isinstance(value, Mapping):
        return {str(key): _bounded(item, string_limit=string_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded(item, string_limit=string_limit) for item in value]
    if isinstance(value, tuple):
        return [_bounded(item, string_limit=string_limit) for item in value]
    return value


def _preview_meta(value: str, limit: int) -> dict[str, Any]:
    return {
        "truncated": len(value) > limit,
        "original_chars": len(value),
        "preview": _preview(value, limit),
        "full_content_location": "native_trajectory_artifact",
    }


def _preview(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = f"\n...[truncated {len(value) - limit} chars in normalized event]...\n"
    head = max(1, (limit - len(marker)) // 2)
    tail = max(1, limit - len(marker) - head)
    return value[:head] + marker + value[-tail:]
