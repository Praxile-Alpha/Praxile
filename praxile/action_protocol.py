"""Action decoding diagnostics without retaining full model reasoning."""
import hashlib

from .json_utils import RobustJSONError, parse_json_object


def decode_action(content):
    evidence = {
        "schema_version": "praxile.action_decode.v1",
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_chars": len(content),
        "reasoning_blocks_removed": 0,
    }
    final = content.lstrip()
    while final.startswith("<think>"):
        reasoning, separator, remainder = final[len("<think>"):].partition("</think>")
        if not separator or "<think>" in reasoning:
            evidence["reason"] = "unclosed_reasoning" if not separator else "nested_reasoning"
            return None, evidence
        evidence["reasoning_blocks_removed"] += 1
        final = remainder.lstrip()
    evidence["final_chars"] = len(final)
    try:
        action = parse_json_object(final)
    except RobustJSONError as error:
        evidence["reason"] = "invalid_json_object" if final else "missing_final_action"
        # RobustJSONError contains parser messages and offsets, never source text.
        evidence["parser_error"] = str(error)[:1000]
        return None, evidence
    evidence["reason"] = "parsed"
    return action, evidence
