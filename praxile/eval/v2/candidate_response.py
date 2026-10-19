"""Versioned submission contract; local candidate validators remain authoritative."""
import json

from ...action_schema import ActionSchemaRegistry

ABSTENTION_REASONS = ("insufficient_evidence", "capability_mismatch", "contradictory_evidence", "no_bounded_intervention")
MISSING_EVIDENCE = ("before_after_observations", "action_parameters", "public_content_constraints",
                    "capability_health", "counterexamples")


def candidate_response_contract(refs, capability=None, *, json_actions=False):
    if json_actions and capability is None:
        raise ValueError("JSON action submission requires an executor profile")
    draft_properties = {
        "content": {"type": "string", "minLength": 1, "maxLength": 6000},
        "hypothesis": {"type": "string", "minLength": 1, "maxLength": 2000},
        "evidence_refs": {"type": "array", "minItems": 1, "maxItems": len(refs),
                          "items": {"type": "string", "enum": list(refs)}},
    }
    if capability is not None:
        registry = ActionSchemaRegistry()
        types = {str: "string", int: "integer", bool: "boolean", dict: "object", list: "array"}
        actions = []
        for name in capability["profile"]["actions"]:
            schema = registry.schemas[name]
            properties = {key: {"type": types[kind]} for section in schema.values() for key, kind in section.items()}
            for key, bounds in capability.get("action_constraints", {}).get(name, {}).items():
                if key in properties:
                    properties[key].update({field: value for field, value in bounds.items()
                                            if field in {"minimum", "maximum", "pattern"}})
            properties["type"] = {"type": "string", "enum": [name]}
            actions.append({"type": "object", "properties": properties,
                            "required": ["type", *schema["required"]], "additionalProperties": False})
        draft_properties["action_examples"] = {"type": "array", "minItems": 1, "maxItems": 8,
                                                "items": {"oneOf": actions}}
        if json_actions:
            draft_properties["action_examples"]["items"] = {"type": "string", "minLength": 2,
                "maxLength": 65536, "description": "Encode one complete strict JSON action object. "
                "The top-level type discriminator and all parameters must match one of the following schemas. "
                "Do not nest parameters inside arguments. Keep integer fields as JSON numbers. "
                "This submission never executes a tool. Action schemas: " + json.dumps(actions, sort_keys=True)}
    draft = {"type": "object", "properties": draft_properties,
             "required": list(draft_properties), "additionalProperties": False}
    propose = {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["propose"]},
        "candidates": {"type": "array", "minItems": 2, "maxItems": 2, "items": draft},
        "composition": draft}, "required": ["decision", "candidates", "composition"], "additionalProperties": False}
    abstain = {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["abstain"]},
        "reason": {"type": "string", "enum": list(ABSTENTION_REASONS)},
        "missing_evidence": {"type": "array", "maxItems": len(MISSING_EVIDENCE),
                             "items": {"type": "string", "enum": list(MISSING_EVIDENCE)}}},
        "required": ["decision", "reason", "missing_evidence"], "additionalProperties": False}
    return {"name": "submit_development_candidates_v2" if json_actions else "submit_development_candidates_v1",
            "schema": {"type": "object", "oneOf": [propose, abstain]}}
