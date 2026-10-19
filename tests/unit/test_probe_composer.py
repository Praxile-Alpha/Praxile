import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.probe_composer import compose_after_development_probes
from praxile.eval.v2.schema import EvalSchemaError
from praxile.trajectory import TrajectoryLogger


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
