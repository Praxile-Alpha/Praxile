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


def measured_setup():
    from praxile.eval.v2.alignment import align_operations
    from praxile.eval.v2.behavior_probe import probe_repetition
    parents, reports, kwargs, calls = setup({"content": '{"decision":"abstain"}'})
    for parent, report in zip(parents, reports):
        parent.expected_effect["measurement"] = {"kind": "repeated_exact_operations", "operation": "search", "direction": "decrease"}
        report["candidate_digest"] = parent.digest
        event = lambda name: SimpleNamespace(event_id=name, type="TOOL_CALL",
            payload={"action": {"type": "search", "pattern": "PRIVATE_SEARCH"}})
        alignment = align_operations([event("a1"), event("a2")], [event("b1")])
        behavior = probe_repetition(parent.expected_effect, alignment)
        behavior["candidate_exposure_verified"] = True
        report["improvement_episodes"][0].update(candidate={"digest": parent.digest},
            behavior_probe=behavior, operation_alignment=alignment, verdict="hurt")
    return parents, reports, kwargs, calls


def test_behavior_feedback_is_replayed_and_not_sent_as_task_success():
    parents, reports, kwargs, calls = measured_setup()
    assert compose_after_development_probes(parents, reports, **kwargs) is None
    payload = json.loads(calls[0][1]["content"])
    result = payload["feedback"][0]["outcomes"][0]
    assert result["verdict"] == "hurt"
    assert result["behavior"]["delta"] == -1
    assert result["behavior"]["task_success_proven"] is False
    assert result["behavior"]["evidence_ref"] in payload["allowed_refs"]
    assert "PRIVATE_SEARCH" not in json.dumps(calls)
    assert "a1" not in payload["feedback"][0]["outcomes"][0]["behavior"]


@pytest.mark.parametrize("invalid", ["delta", "identity", "duplicate_task"])
def test_modified_behavior_feedback_stops_before_model(invalid):
    parents, reports, kwargs, calls = measured_setup()
    episode = reports[0]["improvement_episodes"][0]
    if invalid == "delta":
        episode["behavior_probe"]["delta"] = -99
    elif invalid == "identity":
        episode["candidate"]["digest"] = "wrong"
    else:
        reports[0]["improvement_episodes"].append(episode)
    with pytest.raises(EvalSchemaError):
        compose_after_development_probes(parents, reports, **kwargs)
    assert not calls


def test_unverified_exposure_does_not_supply_measurement_reference():
    parents, reports, kwargs, calls = measured_setup()
    for report in reports:
        report["improvement_episodes"][0]["behavior_probe"]["candidate_exposure_verified"] = False
    compose_after_development_probes(parents, reports, **kwargs)
    payload = json.loads(calls[0][1]["content"])
    assert not any(ref.startswith("probe:") for ref in payload["allowed_refs"])
    assert payload["feedback"][0]["outcomes"][0]["behavior"] == {
        "status": "abstained", "reason": "candidate_exposure_not_established"}


def test_configured_composer_freezes_current_protocol(tmp_path, monkeypatch):
    from praxile.config import Config
    from praxile.eval.v2 import configured_composer
    from praxile.eval.v2.probe_composer import COMPOSER_PROTOCOL
    router = SimpleNamespace(providers={}, route=lambda *args, **kwargs: None,
        describe_route=lambda *args, **kwargs: {"provider": "fixture", "model": "fixture"})
    monkeypatch.setattr(configured_composer, "ModelRouter", lambda config: router)
    scope = {"repositories": ["owner/repo"], "task_signals": ["repair"], "does_not_apply_when": ["unrelated"]}
    with configured_composer.configured_design_router(Config.load(tmp_path), cycle_id="c", applies_to=scope) as (_, identity):
        assert identity["implementation"] == COMPOSER_PROTOCOL == "praxile.probe_composer.v2"


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
