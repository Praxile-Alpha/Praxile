from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from praxile.eval.frozen_native_protocol import validate_protocol
from praxile.eval.native_context import owned_candidate_identity, plan_candidate
from praxile.eval.v2.candidate import ContextCandidate


def materials(tmp_path):
    candidate = ContextCandidate(candidate_id="frozen", version="1", title="Fixture",
        candidate_type="context_policy", context_item={"content": "Inspect before revising."},
        confidence=0, evidence_refs=("event:dev",), expected_effect={"hypothesis": "fixture"},
        source_task_ids=("dev",), applies_to={"repositories": ["docs"],
        "task_signals": ["checklist"], "does_not_apply_when": ["unrelated"]}).to_dict()
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    protocol = {"schema_version": "praxile.frozen_native_protocol.v1", "development_task_ids": ["dev"],
        "heldout_task_ids": ["test-a", "test-b"], "candidate_file_sha256": digest, "repository": "docs"}
    return candidate, path, digest, protocol, SimpleNamespace(task_id="test-a", split="heldout")


def test_heldout_requires_explicit_frozen_protocol_and_preserves_activation(tmp_path):
    value, path, digest, protocol, task = materials(tmp_path)
    with pytest.raises(ValueError):
        owned_candidate_identity(task, path, digest, "docs", development_only=True)
    identity = owned_candidate_identity(task, path, digest, "docs", frozen_protocol=protocol)
    assert identity["track"] == "heldout_frozen"
    assert identity["protocol_digest"].startswith("sha256:")
    assert identity["promotion_eligible"] is False
    assert "heldout_task_ids" not in identity
    activated = plan_candidate(value, "Make a checklist", "docs", development_only=False, heldout_frozen=True)
    assert activated["activation"]["activated"] is True
    excluded = plan_candidate(value, "unrelated checklist", "docs", development_only=False, heldout_frozen=True)
    assert excluded["content"] is None
    with pytest.raises(ValueError):
        owned_candidate_identity(task, path, digest, "docs", development_only=True, frozen_protocol=protocol)


@pytest.mark.parametrize("defect", ["overlap", "unknown_task", "development", "unknown_source", "no_source",
    "heldout_source", "digest", "repository", "duplicate", "empty", "extra_field"])
def test_invalid_protocol_rejected_before_any_execution(tmp_path, defect):
    value, path, digest, protocol, task = materials(tmp_path)
    if defect == "overlap":
        protocol["development_task_ids"].append("test-b")
    elif defect == "unknown_task":
        task.task_id = "unknown"
    elif defect == "development":
        task.split = "development"
    elif defect in {"unknown_source", "no_source", "heldout_source"}:
        value["source_task_ids"] = {"unknown_source": ["unknown"], "no_source": [], "heldout_source": ["test-b"]}[defect]
    elif defect == "digest":
        protocol["candidate_file_sha256"] = "0" * 64
    elif defect == "repository":
        protocol["repository"] = "elsewhere"
    elif defect == "duplicate":
        protocol["heldout_task_ids"].append("test-a")
    elif defect == "empty":
        protocol["development_task_ids"] = []
    else:
        protocol["update_after_task"] = True
    with pytest.raises(ValueError):
        validate_protocol(protocol, task, value, digest, "docs")


def test_worker_heldout_path_uses_only_frozen_context_and_no_long_term_retrieval(tmp_path, monkeypatch):
    from praxile.eval import native_worker
    value, _, _, _, _ = materials(tmp_path)
    calls = []
    class Runtime:
        router = SimpleNamespace(providers={})
        def __init__(self, config):
            pass
        def run(self, instruction, **options):
            calls.append(options)
            return {"experimental_context": {"injected": True}}
    monkeypatch.setattr(native_worker, "AgentRuntime", Runtime)
    monkeypatch.setattr(native_worker, "benchmark_config", lambda *args, **kwargs: None)
    result = native_worker.run_native(tmp_path, "Make a checklist", model="fixture", base_url="unused",
        max_steps=3, development_candidate=deepcopy(value), repository="docs", heldout_frozen=True)
    assert result["experience_mode"] == "heldout_frozen_candidate"
    assert result["development_context"]["track"] == "heldout_frozen"
    assert result["development_context"]["injected"] is True
    assert calls[0]["use_experience"] is False
    assert calls[0]["experimental_context"] == value["context_item"]["content"]
