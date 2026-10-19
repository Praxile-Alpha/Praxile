import hashlib
import json

import pytest

from praxile.eval.native_context import load_candidate, plan_candidate
from praxile.eval.v2.candidate import ContextCandidate
from praxile.task_input import TaskInput


def candidate():
    return ContextCandidate(candidate_id="development", version="1", title="Document hint",
        candidate_type="context_policy", context_item={"content": "Check the page count after revising."},
        confidence=0, evidence_refs=("event:source",), expected_effect={"hypothesis": "Avoid unchecked output"},
        applies_to={"repositories": ["public/docs"], "task_signals": ["one-page checklist"],
                    "does_not_apply_when": ["multi-page guide"]}).to_dict()


def test_scope_checks_public_body_not_environment_instructions():
    task = TaskInput("Produce a one-page checklist", "Use this multi-page guide as context", "public")
    plan = plan_candidate(candidate(), task, "public/docs", development_only=True)
    assert plan["activation"]["activated"] is True
    assert plan["content"] and not plan["permission_granted"] and not plan["promotion_eligible"]
    assert plan_candidate(candidate(), "Produce a multi-page guide", "public/docs",
                          development_only=True)["content"] is None
    assert plan_candidate(candidate(), task, "other/repo", development_only=True)["content"] is None


def test_no_implicit_development_or_unscoped_execution():
    with pytest.raises(ValueError, match="explicit development"):
        plan_candidate(candidate(), "task", "public/docs", development_only=False)
    value = candidate()
    value["applies_to"] = {}
    with pytest.raises(ValueError, match="scope"):
        plan_candidate(value, "task", "public/docs", development_only=True)


def test_candidate_file_integrity_and_duplicate_fields(tmp_path):
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate()))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert load_candidate(path, digest) == candidate()
    with pytest.raises(ValueError, match="digest"):
        load_candidate(path, "0" * 64)
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(ValueError, match="duplicate"):
        load_candidate(path, hashlib.sha256(path.read_bytes()).hexdigest())
