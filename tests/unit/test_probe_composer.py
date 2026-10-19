import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.probe_composer import compose_after_development_probes
from praxile.eval.v2.schema import EvalSchemaError
from praxile.trajectory import TrajectoryLogger
from praxile.eval.v2.executor_profile import profile_context, validate_action_examples


def constrained(parents, reports):
    profile = {"schema_version": "praxile.executor_action_profile.v1", "executor": "praxile",
               "version": "1", "actions": ["check_document_layout"]}
    sample = {"type": "check_document_layout", "path": "out.docx", "max_pages": 1}
    for parent, report in zip(parents, reports):
        parent.context_item["executor_requirements"] = validate_action_examples([sample], profile_context(profile))
        report["candidate_digest"] = parent.digest
    return profile, sample


def setup(response):
    parents = [ContextCandidate(candidate_id=name, version="1", title=name, candidate_type="context_policy",
        context_item={"content": name}, confidence=0, evidence_refs=("event:source",),
        expected_effect={"hypothesis": name}) for name in ("first", "second")]
    reports = [{"track": "development", "candidate_digest": candidate.digest,
        "invariant_check": {"valid": True}, "private_rubric": "DO_NOT_SEND",
        "improvement_episodes": [{"task_id": "dev", "verdict": "no_observed_effect",
                                  "confounders": [], "private_notes": "DO_NOT_SEND"}]} for candidate in parents]
    calls = []
    def chat(messages, **kwargs):
        calls.append(messages)
        return response
    kwargs = dict(router=SimpleNamespace(chat=chat), logger=TrajectoryLogger("composition", {}),
                  candidate_id="combined", applies_to={"repositories": ["owner/repo"]})
    return parents, reports, kwargs, calls


def test_feedback_is_whitelisted_and_abstention_keeps_usage():
    parents, reports, kwargs, calls = setup({"content": '{"decision":"abstain"}', "usage": {"prompt_tokens": 10}})
    assert compose_after_development_probes(parents, reports, **kwargs) is None
    assert "DO_NOT_SEND" not in json.dumps(calls)
    assert kwargs["logger"].data["cost"]["prompt_tokens"] == 10


@pytest.mark.parametrize("invalid", ["track", "digest", "invariants"])
def test_invalid_probes_never_call_model(invalid):
    parents, reports, kwargs, calls = setup({})
    if invalid == "track":
        reports[0]["track"] = "clean"
    elif invalid == "digest":
        reports[0]["candidate_digest"] = "wrong"
    else:
        reports[0]["invariant_check"]["valid"] = False
    with pytest.raises(EvalSchemaError):
        compose_after_development_probes(parents, reports, **kwargs)
    assert not calls


def test_truncated_response_is_not_accepted_even_with_valid_json():
    parents, reports, kwargs, _ = setup({"content": '{"decision":"abstain"}', "finish_reason": "length"})
    with pytest.raises(EvalSchemaError, match="truncated"):
        compose_after_development_probes(parents, reports, **kwargs)
    assert kwargs["logger"].data["development_compositions"][0]["status"] == "failed"


def test_capability_is_retained_and_combination_is_revalidated():
    value = {"decision": "compose", "content": "Render before claiming completion", "hypothesis": "Check real page count",
             "evidence_refs": ["event:source"], "action_examples": [{"type": "check_document_layout", "path": "out.docx", "max_pages": 1}]}
    parents, reports, kwargs, calls = setup({"content": json.dumps(value)})
    profile, _ = constrained(parents, reports)
    candidate = compose_after_development_probes(parents, reports, **kwargs)
    requirements = candidate.context_item["executor_requirements"]
    assert requirements["profile"] == profile
    assert not requirements["execution_verified"]
    assert json.loads(calls[0][1]["content"])["executor_capability"]["profile"] == profile


@pytest.mark.parametrize("invalid", ["mixed", "changed", "forged"])
def test_parent_profile_mismatch_fails_before_model(invalid):
    parents, reports, kwargs, calls = setup({})
    constrained(parents, reports)
    if invalid == "mixed":
        parents[1].context_item.pop("executor_requirements")
    elif invalid == "changed":
        requirement = parents[1].context_item["executor_requirements"]
        profile = {**requirement["profile"], "version": "2"}
        parents[1].context_item["executor_requirements"] = validate_action_examples(requirement["action_examples"], profile_context(profile))
    else:
        parents[1].context_item["executor_requirements"]["profile_digest"] = "forged"
    with pytest.raises(EvalSchemaError):
        compose_after_development_probes(parents, reports, **kwargs)
    assert not calls


def test_composer_cannot_invent_an_action_outside_parent_profile():
    value = {"decision": "compose", "content": "Estimate", "hypothesis": "Estimate", "evidence_refs": ["event:source"],
             "action_examples": [{"type": "estimate_pages"}]}
    parents, reports, kwargs, _ = setup({"content": json.dumps(value)})
    constrained(parents, reports)
    with pytest.raises(EvalSchemaError, match="not declared"):
        compose_after_development_probes(parents, reports, **kwargs)
