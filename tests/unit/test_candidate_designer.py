from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.candidate_designer import design_development_candidates
from praxile.eval.v2.schema import EvalSchemaError
from praxile.trajectory import TrajectoryLogger


def proposal():
    return {"decision": "propose", "candidates": [
        {"content": "Read focused files", "hypothesis": "Reduce exploration", "evidence_refs": ["event:e1"]},
        {"content": "Run targeted tests", "hypothesis": "Find regressions earlier", "evidence_refs": ["event:e1"]}],
        "composition": {"content": "Test then read focused files", "hypothesis": "Tests guide file selection",
                        "evidence_refs": ["event:e1"]}}


def inputs(response):
    calls = []
    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return response
    return dict(evidence=[{"task_id": "dev", "event_id": "e1", "observation": "Repeated broad search failed"}],
        development_task_ids=["dev"], heldout_task_ids=["heldout"],
        applies_to={"repositories": ["owner/repo"], "task_signals": ["regression"],
                    "does_not_apply_when": ["unrelated repository"]}, design_id="design",
        router=SimpleNamespace(chat=chat), logger=TrajectoryLogger("design-task", {})), calls


def test_designer_calls_configurable_role_and_produces_valid_frozen_drafts():
    kwargs, calls = inputs({"content": json.dumps(proposal()), "usage": {"prompt_tokens": 20}})
    result = design_development_candidates(**kwargs, private=True)
    assert len(calls) == 1 and calls[0][1]["purpose"] == "proposal_composer"
    assert calls[0][1]["private"] is True
    assert "heldout" not in json.dumps(calls[0][0])
    for item in [*result["candidates"], result["composition"]]:
        candidate = ContextCandidate.from_dict(item)
        assert candidate.source_task_ids == ("dev",)
        assert candidate.applies_to == kwargs["applies_to"]
        assert candidate.confidence == 0.0
    assert result["promotion_eligible"] is False
    assert kwargs["logger"].data["cost"]["usage_evidence"]["complete_tokens"]["completion_tokens"] is None
    assert kwargs["logger"].data["development_candidate_designs"][0]["status"] == "drafted"


def test_abstention_is_normal_no_entry():
    kwargs, _ = inputs({"content": '{"decision":"abstain"}'})
    result = design_development_candidates(**kwargs)
    assert result["decision"] == "NO_ENTRY" and not result["candidates"]


@pytest.mark.parametrize("failure", ["unknown_ref", "duplicate", "extra_field", "truncated", "bad_json"])
def test_invalid_drafts_never_become_candidates_and_usage_is_kept(failure):
    value = proposal()
    if failure == "unknown_ref":
        value["composition"]["evidence_refs"] = ["event:heldout"]
    if failure == "duplicate":
        value["candidates"][1] = deepcopy(value["candidates"][0])
    if failure == "extra_field":
        value["composition"]["permissions"] = ["all"]
    kwargs, _ = inputs({"content": "not json" if failure == "bad_json" else json.dumps(value),
                       "finish_reason": "length" if failure == "truncated" else "stop",
                       "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    with pytest.raises(ValueError):
        design_development_candidates(**kwargs)
    assert kwargs["logger"].data["cost"]["prompt_tokens"] == 20
    assert kwargs["logger"].data["development_candidate_designs"][0]["status"] == "failed"


@pytest.mark.parametrize("invalid", ["heldout", "rubric", "scope"])
def test_invalid_input_rejected_before_model_call(invalid):
    kwargs, calls = inputs({})
    if invalid == "heldout":
        kwargs["evidence"][0]["task_id"] = "heldout"
    elif invalid == "rubric":
        kwargs["evidence"][0]["rubric"] = "private"
    else:
        kwargs["applies_to"]["task_signals"] = []
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs)
    assert calls == []
