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


def test_explicit_request_timeout_reaches_router_and_trajectory():
    kwargs, calls = inputs({"content": '{"decision":"abstain"}', "usage": {}})
    design_development_candidates(**kwargs, request_timeout_seconds=120)
    assert calls[0][1]["timeout"] == 120
    assert kwargs["logger"].data["development_candidate_designs"][0]["timeout_seconds"] == 120
    assert kwargs["logger"].data["development_candidate_designs"][0]["input_inventory"] == {
        "schema_version": "praxile.design_input_inventory.v1", "evidence_count": 1,
        "public_task_context_supplied": False, "executor_profile_supplied": False,
        "evidence_sufficiency_verified": False}


def test_one_explicit_repair_preserves_failed_attempt_and_both_costs():
    bad = proposal()
    bad["candidates"][0]["evidence_refs"] = ["event:invented"]
    kwargs, _ = inputs({})
    calls = []
    def chat(messages, **options):
        calls.append((messages, options))
        return {"content": json.dumps(bad if len(calls) == 1 else proposal()),
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    kwargs["router"] = SimpleNamespace(chat=chat)
    checkpoints = []
    result = design_development_candidates(**kwargs, repair_attempts=1, response_mode="function",
        attempt_checkpoint=lambda: checkpoints.append(deepcopy(kwargs["logger"].data)))
    assert result["decision"] == "DRAFTS_REQUIRE_EXPERIMENT"
    assert result["promotion_eligible"] is False
    records = kwargs["logger"].data["development_candidate_designs"]
    assert [item["status"] for item in records] == ["failed", "drafted"]
    assert [item["submission_attempt"] for item in records] == [0, 1]
    assert kwargs["logger"].data["cost"]["model_calls"] == 2
    assert kwargs["logger"].data["cost"]["prompt_tokens"] == 20
    assert len(checkpoints) == 2
    assert checkpoints[0]["development_candidate_designs"][0]["status"] == "failed"
    assert checkpoints[0]["cost"]["model_calls"] == 1
    assert calls[0][1]["response_contract"] == calls[1][1]["response_contract"]
    repaired = json.loads(calls[1][0][1]["content"])
    assert repaired["allowed_refs"] == ["event:e1"]
    assert repaired["validation_repair"]["validation_stage"] == "candidate_evidence_refs"
    assert repaired["validation_repair"]["validation_diagnostic"]["candidate_position"] == "first"
    assert repaired["validation_repair"]["validation_diagnostic"]["citations_must_exactly_match_allowed_refs"] is True
    assert "previous_submission" not in json.dumps(records)


def test_repair_passes_safe_specific_action_diagnostics_without_exception_text():
    bad = proposal()
    for draft in [*bad["candidates"], bad["composition"]]:
        draft["action_examples"] = [{"type": "read_document", "path": "doc.docx", "start": "PRIVATE", "limit": "PRIVATE"}]
    kwargs, _ = inputs({})
    calls = []
    def chat(messages, **options):
        calls.append(messages)
        return {"content": json.dumps(bad if len(calls) == 1 else {"decision": "abstain"})}
    kwargs["router"] = SimpleNamespace(chat=chat)
    result = design_development_candidates(**kwargs, repair_attempts=1,
        executor_profile={"schema_version": "praxile.executor_action_profile.v2",
            "executor": "praxile", "version": "1", "actions": ["read_document"]})
    assert result["decision"] == "NO_ENTRY"
    feedback = json.loads(calls[1][1]["content"])["validation_repair"]
    diagnostic = feedback["validation_diagnostic"]
    assert diagnostic["action"]["field_error_counts"] == {"start": 2, "limit": 2}
    assert diagnostic["integer_fields_must_be_json_numbers_not_strings"] is True
    assert "PRIVATE" not in json.dumps(diagnostic)
    assert "PRIVATE" in feedback["previous_submission"]


@pytest.mark.parametrize("repairs, expected", [(0, 1), (1, 2)])
def test_invalid_drafts_stop_at_explicit_repair_budget(repairs, expected):
    kwargs, calls = inputs({"content": '{"decision":"propose"}'})
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs, repair_attempts=repairs)
    assert len(calls) == expected


def test_repair_never_retries_transport_failure():
    kwargs, _ = inputs({})
    calls = []
    def chat(*args, **options):
        calls.append(options)
        raise TimeoutError("unavailable")
    kwargs["router"] = SimpleNamespace(chat=chat)
    with pytest.raises(TimeoutError):
        design_development_candidates(**kwargs, repair_attempts=1)
    assert len(calls) == 1


def test_failed_checkpoint_prevents_repair_request():
    kwargs, calls = inputs({"content": '{"decision":"propose"}'})
    def fail_checkpoint():
        raise OSError("disk full")
    with pytest.raises(OSError, match="disk full"):
        design_development_candidates(**kwargs, repair_attempts=1, attempt_checkpoint=fail_checkpoint)
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    {"content": json.dumps(proposal()), "finish_reason": "length"},
    {"content": "not JSON"},
    {"content": json.dumps({"decision": "propose", "extra": "x" * 65536})},
])
def test_truncated_unparseable_or_oversized_response_not_repaired(response):
    kwargs, calls = inputs(response)
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs, repair_attempts=1)
    assert len(calls) == 1


@pytest.mark.parametrize("value", [True, -1, 2, "1"])
def test_invalid_repair_budget_rejected_before_model(value):
    kwargs, calls = inputs({})
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs, repair_attempts=value)
    assert calls == []


def test_function_contract_is_forwarded_and_local_validation_still_applies():
    kwargs, calls = inputs({"content": json.dumps(proposal()), "finish_reason": "tool_calls"})
    result = design_development_candidates(**kwargs, response_mode="function")
    contract = calls[0][1]["response_contract"]
    assert contract["name"] == "submit_development_candidates_v1"
    assert contract["schema"]["oneOf"][0]["properties"]["candidates"]["minItems"] == 2
    assert contract["schema"]["oneOf"][0]["properties"]["composition"]["properties"]["evidence_refs"]["items"]["enum"] == ["event:e1"]
    assert result["decision"] == "DRAFTS_REQUIRE_EXPERIMENT"
    assert kwargs["logger"].data["development_candidate_designs"][0]["response_protocol"]["mode"] == "function"
    invalid = proposal()
    invalid["composition"]["evidence_refs"] = ["event:invented"]
    kwargs, _ = inputs({"content": json.dumps(invalid), "finish_reason": "tool_calls"})
    with pytest.raises(EvalSchemaError, match="supplied evidence"):
        design_development_candidates(**kwargs, response_mode="function")


def test_function_action_contract_declares_discriminator_and_parameters():
    from praxile.eval.v2.candidate_response import candidate_response_contract
    from praxile.eval.v2.executor_profile import profile_context
    capability = profile_context({"schema_version": "praxile.executor_action_profile.v2",
        "executor": "praxile", "version": "1", "actions": ["read_document"]})
    contract = candidate_response_contract(["event:e1"], capability)
    action = contract["schema"]["oneOf"][0]["properties"]["composition"]["properties"]["action_examples"]["items"]["oneOf"][0]
    assert action["required"] == ["type", "path"]
    assert action["properties"]["type"]["enum"] == ["read_document"]
    assert action["properties"]["limit"] == {"type": "integer", "minimum": 1, "maximum": 20}
    assert action["additionalProperties"] is False


@pytest.mark.parametrize("result", [
    {"decision": "PRIVATE", "candidates": [], "composition": {}},
    {"decision": "propose", "PRIVATE": "PRIVATE"},
    {"decision": "propose", "candidates": "PRIVATE", "composition": {}},
])
def test_proposal_shape_diagnostics_do_not_record_unknown_names_or_values(result):
    kwargs, _ = inputs({"content": json.dumps(result), "finish_reason": "tool_calls"})
    with pytest.raises(EvalSchemaError, match="exactly two"):
        design_development_candidates(**kwargs, response_mode="function")
    record = kwargs["logger"].data["development_candidate_designs"][0]
    diagnostic = record["proposal_validation_diagnostic"]
    assert "PRIVATE" not in json.dumps(diagnostic)
    assert diagnostic["missing_fields"] == sorted({"decision", "candidates", "composition"} - set(result))
    assert record["failure_stage"] == "proposal_schema"
    assert record["promotion_eligible"] is False


@pytest.mark.parametrize("value", [True, 0, 301, "120"])
def test_invalid_request_timeout_never_calls_router(value):
    kwargs, calls = inputs({})
    with pytest.raises(EvalSchemaError, match="request timeout"):
        design_development_candidates(**kwargs, request_timeout_seconds=value)
    assert calls == []


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
    assert result["abstention"]["reason"] == "unspecified"


def test_explicit_budget_is_recorded_and_never_automatically_expanded():
    kwargs, calls = inputs({"content": "", "finish_reason": "length",
        "usage": {"completion_tokens": 8000, "completion_tokens_details": {"reasoning_tokens": 8000}}})
    with pytest.raises(EvalSchemaError, match="truncated"):
        design_development_candidates(**kwargs, max_output_tokens=8000)
    assert len(calls) == 1 and calls[0][1]["max_tokens"] == 8000
    record = kwargs["logger"].data["development_candidate_designs"][0]
    assert record["max_output_tokens"] == 8000
    assert record["reasoning_tokens"] == 8000 and record["final_content_chars"] == 0
    assert record["truncation_diagnostic"] == "reasoning_consumed_budget_without_final_content"
    assert record["status"] == "failed"


@pytest.mark.parametrize("budget", [True, 0, 255, 16385, "8000"])
def test_invalid_output_budget_rejected_before_model(budget):
    kwargs, calls = inputs({"content": '{"decision":"abstain"}'})
    with pytest.raises(EvalSchemaError, match="budget"):
        design_development_candidates(**kwargs, max_output_tokens=budget)
    assert calls == []


def test_failed_transport_evidence_survives_wrapped_model_error():
    from praxile.http_transport import HTTPTransportUnavailable
    kwargs, _ = inputs({})
    evidence = {"schema_version": "praxile.http_attempts.v1", "attempt_count": 2,
                "failed_attempt_usage_unknown": True}
    def fail(*args, **options):
        error = HTTPTransportUnavailable("private error body")
        error.transport_evidence = evidence
        try:
            raise error
        except HTTPTransportUnavailable as source:
            raise RuntimeError("wrapped") from source
    kwargs["router"].chat = fail
    with pytest.raises(RuntimeError):
        design_development_candidates(**kwargs)
    record = kwargs["logger"].data["development_candidate_designs"][0]
    assert record["transport_evidence"] == evidence
    assert record["transport_evidence_scope"] == "last_provider_exception_chain"
    assert "private error body" not in json.dumps(record)


def test_bounded_abstention_diagnostics_are_unverified_and_durable(tmp_path):
    from praxile.eval.v2.candidate_design_run import _finish
    kwargs, calls = inputs({"content": json.dumps({"decision": "abstain",
        "reason": "insufficient_evidence", "missing_evidence": ["public_content_constraints"]})})
    result = design_development_candidates(**kwargs)
    assert len(calls) == 1
    assert result["abstention"]["verified"] is False
    assert result["abstention"]["source"] == "model_self_report"
    assert kwargs["logger"].data["development_candidate_designs"][0]["abstention"] == result["abstention"]
    report = _finish(tmp_path, "design", result)
    assert report["abstention"] == result["abstention"]
    assert not report["experiments_started"] and not report["candidate_files"]


@pytest.mark.parametrize("update", [
    {"reason": []}, {"reason": "send private rubric"},
    {"missing_evidence": ["credentials"]}, {"missing_evidence": "action_parameters"},
    {"extra": "unbounded text"},
])
def test_invalid_abstention_diagnostics_rejected(update):
    payload = {"decision": "abstain", "reason": "insufficient_evidence", "missing_evidence": []}
    payload.update(update)
    kwargs, _ = inputs({"content": json.dumps(payload)})
    with pytest.raises(EvalSchemaError, match="abstention"):
        design_development_candidates(**kwargs)
    assert kwargs["logger"].data["development_candidate_designs"][0]["failure_stage"] == "abstention_schema"


@pytest.mark.parametrize("mutation,stage,position", [
    (lambda p: p["candidates"][0].update(extra="SECRET"), "candidate_fields", "first"),
    (lambda p: p["candidates"][1].update(content=""), "candidate_text_bounds", "second"),
    (lambda p: p["composition"].update(evidence_refs=["event:SECRET"]), "candidate_evidence_refs", "composition"),
    (lambda p: p["candidates"][1].update(content=p["candidates"][0]["content"]), "candidate_distinctness", None),
])
def test_validation_stage_logged_without_response_content(mutation, stage, position):
    value = proposal()
    mutation(value)
    kwargs, _ = inputs({"content": json.dumps(value)})
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs)
    record = kwargs["logger"].data["development_candidate_designs"][0]
    assert record["failure_stage"] == stage and record["candidate_position"] == position
    assert "SECRET" not in json.dumps(record)


def test_reasoning_json_is_not_mistaken_for_final_candidate():
    kwargs, _ = inputs({"content": '<think>{"decision":"propose"} private thought</think>\n{"decision":"abstain"}'})
    result = design_development_candidates(**kwargs)
    assert result["decision"] == "NO_ENTRY"
    decode = kwargs["logger"].data["development_candidate_designs"][0]["response_decode"]
    assert decode["reasoning_blocks_removed"] == 1
    assert "private thought" not in json.dumps(decode)


def test_unclosed_reasoning_never_becomes_a_candidate():
    kwargs, _ = inputs({"content": '<think>{"decision":"abstain"}'})
    with pytest.raises(EvalSchemaError):
        design_development_candidates(**kwargs)
    assert kwargs["logger"].data["development_candidate_designs"][0]["response_decode"]["reason"] == "unclosed_reasoning"


def test_capability_profile_is_in_prompt_and_schema_checked_on_each_draft():
    value = proposal()
    for draft in [*value["candidates"], value["composition"]]:
        draft["action_examples"] = [{"type": "check_document_layout", "path": "out.docx", "max_pages": 1}]
    kwargs, calls = inputs({"content": json.dumps(value)})
    profile = {"schema_version": "praxile.executor_action_profile.v1", "executor": "praxile", "version": "1",
               "actions": ["check_document_layout"]}
    result = design_development_candidates(**kwargs, executor_profile=profile)
    assert "executor_capability" in json.loads(calls[0][0][1]["content"])
    assert result["composition"]["context_item"]["executor_requirements"]["schema_validated"] is True
    value["composition"]["action_examples"][0]["type"] = "estimate_pages"
    kwargs, _ = inputs({"content": json.dumps(value)})
    with pytest.raises(EvalSchemaError, match="not declared"):
        design_development_candidates(**kwargs, executor_profile=profile)
    diagnostic = kwargs["logger"].data["development_candidate_designs"][0]["action_validation_diagnostic"]
    assert diagnostic["reason"] == "undeclared_action" and diagnostic["example_index"] == 0
    assert "estimate_pages" not in json.dumps(diagnostic)


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
