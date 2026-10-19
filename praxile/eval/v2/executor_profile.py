"""Declared native tool interfaces, not runtime permissions or health attestations."""
from copy import deepcopy
import hashlib

from ...action_schema import ActionSchemaRegistry
from ...document_spec import docx_spec_prompt
from .schema import EvalSchemaError, canonical_json


def profile_context(profile):
    if (not isinstance(profile, dict)
            or set(profile) != {"schema_version", "executor", "version", "actions"}
            or profile["schema_version"] not in {"praxile.executor_action_profile.v1", "praxile.executor_action_profile.v2"}
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
    if profile["schema_version"] == "praxile.executor_action_profile.v2":
        bounds = {
            "read_document": {"start": {"minimum": 1, "maximum": 100000},
                              "limit": {"minimum": 1, "maximum": 20},
                              "char_offset": {"minimum": 0, "maximum": 10000000}},
            "check_document_layout": {"max_pages": {"minimum": 1, "maximum": 2000}},
            "revise_docx": {"expected_sha256": {"pattern": "^[0-9a-f]{64}$",
                "source": "current document observation; refresh before execution"}},
        }
        description["action_constraints"] = {name: bounds[name] for name in names if name in bounds}
        description["constraints_complete"] = False
    description["digest"] = "sha256:" + hashlib.sha256(canonical_json(description).encode()).hexdigest()
    return description


def validate_action_examples(examples, capability):
    if not isinstance(examples, list) or not 1 <= len(examples) <= 8:
        _invalid_example("invalid_example_list")
    registry = ActionSchemaRegistry()
    for index, example in enumerate(examples):
        if not isinstance(example, dict) or example.get("type") not in capability["profile"]["actions"]:
            _invalid_example("undeclared_action", index)
        valid, errors = registry.validate(example)
        if not valid:
            schema = registry.schemas[example["type"]]
            _invalid_example("schema_mismatch", index, action_type=example["type"],
                error_count=len(errors), missing_required_fields=[key for key in schema["required"] if key not in example],
                field_error_counts={key: sum(error.startswith(example["type"] + "." + key + " ") for error in errors)
                    for key in {**schema["required"], **schema["optional"]}
                    if any(error.startswith(example["type"] + "." + key + " ") for error in errors)},
                unknown_field_count=len(set(example) - {"type", *schema["required"], *schema["optional"]}))
    return {"profile_digest": capability["digest"], "executor": capability["profile"]["executor"],
            "profile": deepcopy(capability["profile"]), "action_examples": deepcopy(examples),
            "schema_validated": True, "execution_verified": False, "permission_granted": False}


def _invalid_example(reason, index=None, **details):
    messages = {"invalid_example_list": "profile-constrained draft requires 1..8 action_examples",
                "undeclared_action": "candidate action is not declared by executor profile",
                "schema_mismatch": "candidate action schema mismatch"}
    error = EvalSchemaError(messages[reason])
    error.action_validation_diagnostic = {"schema_version": "praxile.candidate_action_validation.v1",
        "reason": reason, "example_index": index, **details}
    raise error


def common_parent_capability(candidates):
    requirements = [candidate.context_item.get("executor_requirements") for candidate in candidates]
    if not any(item is not None for item in requirements):
        return None
    if not all(isinstance(item, dict) for item in requirements):
        raise EvalSchemaError("cannot mix constrained and unconstrained parents")
    capabilities = []
    for item in requirements:
        current = profile_context(item.get("profile"))
        if validate_action_examples(item.get("action_examples"), current) != item:
            raise EvalSchemaError("parent executor requirement validation or digest changed")
        capabilities.append(current)
    if len({item["digest"] for item in capabilities}) != 1:
        raise EvalSchemaError("composition requires identical parent executor capabilities")
    return capabilities[0]
