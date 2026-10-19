"""Bounded optional reasoning controls; no overrides of messages or budgets."""
from copy import deepcopy


def validate_request_options(value):
    if not isinstance(value, dict) or set(value) - {"thinking", "reasoning_effort", "reasoning_split"}:
        raise ValueError("request_options permits only thinking, reasoning_effort and reasoning_split")
    if "reasoning_effort" in value and (not isinstance(value["reasoning_effort"], str)
            or value["reasoning_effort"] not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}):
        raise ValueError("invalid request_options.reasoning_effort")
    if "reasoning_split" in value and type(value["reasoning_split"]) is not bool:
        raise ValueError("request_options.reasoning_split must be boolean")
    if "thinking" in value:
        thinking = value["thinking"]
        if (not isinstance(thinking, dict) or set(thinking) != {"type"}
                or not isinstance(thinking["type"], str) or thinking["type"] not in {"enabled", "disabled"}):
            raise ValueError("request_options.thinking must contain only type=enabled|disabled")
    return deepcopy(value)
