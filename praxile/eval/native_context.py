"""Opt-in, development-only candidate planning for the native worker."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .v2.activation import ContextActivationGate
from .v2.candidate import ContextCandidate
from .v2.executor_profile import common_parent_capability
from ..task_input import TaskInput


def load_candidate(path: Path, digest: str):
    if path.resolve(strict=True) != path.absolute() or not path.is_file():
        raise ValueError("candidate must be a regular file without symlink components")
    with path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("candidate size or digest mismatch")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate candidate field")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("candidate must be an object")
    return value


def plan_candidate(value, instruction, repository, *, development_only):
    if development_only is not True or not isinstance(repository, str) or not repository.strip():
        raise ValueError("native candidate execution requires explicit development mode and repository identity")
    candidate = ContextCandidate.from_dict(value)
    if candidate.representation_options:
        raise ValueError("native development channel requires a single frozen context representation")
    if set(candidate.context_item) - {"content", "executor_requirements"}:
        raise ValueError("unsupported native candidate context fields")
    for key in ("repositories", "task_signals", "does_not_apply_when"):
        entries = candidate.applies_to.get(key)
        if not isinstance(entries, list) or not entries or any(not isinstance(v, str) or not v.strip() for v in entries):
            raise ValueError("native candidate requires explicit scope and anti-scope")
    common_parent_capability([candidate])
    content = candidate.context_item["content"]
    if len(content) > 6000:
        raise ValueError("native candidate context exceeds 6000 characters")
    body = instruction.body if isinstance(instruction, TaskInput) else instruction
    task_digest = hashlib.sha256(body.encode()).hexdigest()
    task = SimpleNamespace(task_id="native-" + task_digest[:16], instruction=body,
        metadata={}, repository=SimpleNamespace(repo=repository))
    activation = ContextActivationGate().evaluate(candidate, task).to_dict()
    return {"schema_version": "praxile.native_development_context.v1", "track": "development",
        "candidate_digest": candidate.digest, "task_body_sha256": task_digest,
        "activation": activation, "content": content if activation["activated"] else None,
        "promotion_eligible": False, "permission_granted": False}
