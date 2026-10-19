"""Auditable context configuration changes, not executable-code or effect claims."""
from collections.abc import Mapping
from copy import deepcopy
import hashlib

from .schema import canonical_json


def context_component_diff(baseline, candidate):
    result = {"schema_version": "praxile.context_component_diff.v1",
              "status": "unavailable", "scope": "frozen_policy_context",
              "runtime_application_verified": False,
              "source": "frozen experiment manifests"}
    contexts = []
    for manifest in (baseline, candidate):
        reproducibility = getattr(manifest, "reproducibility", None)
        policy = reproducibility.get("policy") if isinstance(reproducibility, Mapping) else None
        context = policy.get("context") if isinstance(policy, Mapping) else None
        if not isinstance(context, list) or any(not isinstance(item, Mapping) for item in context):
            return {**result, "reason": "context_missing_or_invalid"}
        contexts.append(deepcopy(context))
    before, after = contexts
    try:
        encoded = [canonical_json(value).encode("utf-8") for value in contexts]
    except (TypeError, ValueError):
        return {**result, "reason": "context_not_serializable"}
    # One replacement is exact for additions, removals and ordering changes.
    patch = [] if encoded[0] == encoded[1] else [
        {"op": "test", "path": "/context", "value": before},
        {"op": "replace", "path": "/context", "value": after},
    ]
    return {**result, "status": "recorded", "reason": "exact_context_configuration_diff",
            "before_sha256": hashlib.sha256(encoded[0]).hexdigest(),
            "after_sha256": hashlib.sha256(encoded[1]).hexdigest(),
            "changed": bool(patch), "json_patch": patch,
            "limitations": ["Contains candidate context; keep with private experiment evidence.",
                            "Does not prove which representation reached the executor or changed behavior."]}
