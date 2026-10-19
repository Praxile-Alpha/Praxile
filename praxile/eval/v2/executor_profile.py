"""Declared native tool interfaces, not runtime permissions or health attestations."""
from copy import deepcopy
import hashlib

from ...action_schema import ActionSchemaRegistry
from ...document_spec import docx_spec_prompt
from .schema import EvalSchemaError, canonical_json


def profile_context(profile):
    if (not isinstance(profile, dict)
            or set(profile) != {"schema_version", "executor", "version", "actions"}
            or profile["schema_version"] != "praxile.executor_action_profile.v1"
            or profile["executor"] != "praxile"
            or not isinstance(profile["version"], str) or not profile["version"].strip()):
        raise EvalSchemaError("expected a versioned native Praxile executor action profile")
    registry = ActionSchemaRegistry()
    names = profile["actions"]
    if (not isinstance(names, list) or not names or len(names) > len(registry.schemas)
            or any(not isinstance(name, str) or name not in registry.schemas or name == "batch" for name in names)
            or len(set(names)) != len(names)):
        raise EvalSchemaError("profile actions must be unique supported native actions; batch is not supported here")
    interfaces = {name: {section: {key: value.__name__ for key, value in schema.items()}
                        for section, schema in registry.schemas[name].items()} for name in names}
    description = {"profile": deepcopy(profile), "interfaces": interfaces,
                   "document_constraints": docx_spec_prompt() if set(names) & {"create_docx", "revise_docx"} else None,
                   "permission_granted": False, "runtime_availability_verified": False}
    description["digest"] = "sha256:" + hashlib.sha256(canonical_json(description).encode()).hexdigest()
    return description


def validate_action_examples(examples, capability):
    if not isinstance(examples, list) or not 1 <= len(examples) <= 8:
        raise EvalSchemaError("profile-constrained draft requires 1..8 action_examples")
    registry = ActionSchemaRegistry()
    for example in examples:
        if not isinstance(example, dict) or example.get("type") not in capability["profile"]["actions"]:
            raise EvalSchemaError("candidate action is not declared by executor profile")
        valid, errors = registry.validate(example)
        if not valid:
            raise EvalSchemaError("candidate action schema mismatch: " + "; ".join(errors))
    return {"profile_digest": capability["digest"], "executor": capability["profile"]["executor"],
            "profile": deepcopy(capability["profile"]), "action_examples": deepcopy(examples),
            "schema_validated": True, "execution_verified": False, "permission_granted": False}
