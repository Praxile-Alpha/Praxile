"""Opt-in result submission using a named function; never executes a tool."""
from copy import deepcopy
import json
import re


def validate_response_contract(value):
    if (not isinstance(value, dict) or set(value) != {"name", "schema"}
            or not isinstance(value["name"], str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", value["name"])
            or not isinstance(value["schema"], dict) or value["schema"].get("type") != "object"):
        raise ValueError("response contract requires a safe function name and object schema")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("response contract must be finite JSON") from error
    if len(encoded.encode()) > 65536:
        raise ValueError("response contract exceeds 64 KiB")
    pending = [value["schema"]]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "$ref" in item or "$dynamicRef" in item:
                raise ValueError("response contract references are not supported")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return deepcopy(value)


def submission_request(contract):
    contract = validate_response_contract(contract)
    return {"tools": [{"type": "function", "function": {
        "name": contract["name"], "description": "Submit the structured result; this function is not executed.",
        "parameters": contract["schema"]}}],
        "tool_choice": {"type": "function", "function": {"name": contract["name"]}}}


def submission_content(message, contract):
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        raise ValueError("expected exactly one structured result submission")
    call = calls[0]
    function = call.get("function")
    if (call.get("type") != "function" or not isinstance(function, dict)
            or function.get("name") != contract["name"] or not isinstance(function.get("arguments"), str)):
        raise ValueError("structured result function mismatch")
    content = function["arguments"]
    parse_submission_object(content)
    return content


def parse_submission_object(content):
    """Decode strict result JSON without repair, coercion or tool execution."""
    if not isinstance(content, str):
        raise ValueError("structured result must be a JSON string")
    if len(content.encode()) > 1048576:
        raise ValueError("structured result exceeds 1 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate structured result field")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite structured result value")
    try:
        result = json.loads(content, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, RecursionError) as error:
        raise ValueError("structured result arguments must be strict JSON") from error
    if not isinstance(result, dict):
        raise ValueError("structured result arguments must be an object")
    return result
