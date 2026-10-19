import pytest

from praxile.eval.v2.executor_profile import profile_context, validate_action_examples
from praxile.eval.v2.schema import EvalSchemaError


def profile():
    return {"schema_version": "praxile.executor_action_profile.v1", "executor": "praxile",
            "version": "1", "actions": ["revise_docx", "check_document_layout"]}


def action():
    return {"type": "revise_docx", "path": "output.docx", "expected_sha256": "a" * 64,
            "document": {"layout": "compact", "blocks": [{"type": "paragraph", "text": "example"}]}}


def test_profile_carries_real_docx_limits_and_does_not_grant_execution():
    capability = profile_context(profile())
    assert "0.6-inch" in capability["document_constraints"]
    assert "does not guarantee one page" in capability["document_constraints"]
    validated = validate_action_examples([action()], capability)
    assert validated["schema_validated"] is True
    assert not validated["execution_verified"] and not validated["permission_granted"]


@pytest.mark.parametrize("invalid", ["margins", "estimate", "font", "batch"])
def test_invented_tools_or_layout_fields_are_rejected(invalid):
    sample = action()
    if invalid in {"estimate", "batch"}:
        sample = {"type": "estimate_pages" if invalid == "estimate" else "batch", "actions": []}
    else:
        sample["document"][invalid] = 0.5 if invalid == "margins" else 9
    with pytest.raises(EvalSchemaError):
        validate_action_examples([sample], profile_context(profile()))


def test_profile_digest_changes_with_allowed_actions():
    first = profile()
    second = {**first, "actions": ["check_document_layout"]}
    assert profile_context(first)["digest"] != profile_context(second)["digest"]


def test_v2_constraints_match_registry_and_leave_v1_shape_unchanged():
    from praxile.action_schema import ActionSchemaRegistry
    old = {**profile(), "actions": ["read_document", "check_document_layout", "revise_docx"]}
    legacy = profile_context(old)
    assert set(legacy) == {"profile", "interfaces", "document_constraints", "permission_granted",
                           "runtime_availability_verified", "digest"}
    current = profile_context({**old, "schema_version": "praxile.executor_action_profile.v2"})
    assert current["digest"] != legacy["digest"]
    assert current["constraints_complete"] is False
    registry = ActionSchemaRegistry()
    for kind in ("read_document", "check_document_layout"):
        for name, bounds in current["action_constraints"][kind].items():
            for value in (bounds["minimum"], bounds["maximum"]):
                assert registry.validate({"type": kind, "path": "input.docx", name: value})[0]
            for value in (bounds["minimum"] - 1, bounds["maximum"] + 1, True):
                assert not registry.validate({"type": kind, "path": "input.docx", name: value})[0]


def test_validation_diagnostic_does_not_export_unknown_names_or_values():
    sample = {"type": "revise_docx", "PRIVATE_KEY": "PRIVATE_VALUE"}
    with pytest.raises(EvalSchemaError) as caught:
        validate_action_examples([sample], profile_context(profile()))
    diagnostic = caught.value.action_validation_diagnostic
    assert diagnostic["reason"] == "schema_mismatch"
    assert diagnostic["example_index"] == 0
    assert diagnostic["action_type"] == "revise_docx"
    assert diagnostic["unknown_field_count"] == 1
    assert set(diagnostic["missing_required_fields"]) == {"path", "document", "expected_sha256"}
    assert "PRIVATE" not in str(diagnostic) + str(caught.value)


def test_native_candidate_cannot_enter_different_executor(tmp_path):
    from praxile.adapters import AdapterPolicy, FixtureAgentAdapter
    from praxile.eval.v2.candidate import ContextCandidate
    from praxile.eval.v2.experiment import ControlledABExperiment
    candidate = ContextCandidate(candidate_id="native", version="1", title="native",
        candidate_type="context_policy", context_item={"content": "Use compact layout",
            "executor_requirements": validate_action_examples([action()], profile_context(profile()))},
        confidence=0, evidence_refs=("event:e",), expected_effect={"hypothesis": "fit"})
    with pytest.raises(EvalSchemaError, match="selected adapter"):
        ControlledABExperiment(tmp_path, None).run(None, adapter=FixtureAgentAdapter(), evaluator=None,
            baseline_policy=AdapterPolicy(policy_id="base"), candidate=candidate, model={}, experiment_id="test")


def test_cycle_rejects_precomposed_candidate_that_drops_parent_capabilities(tmp_path):
    from praxile.adapters import AdapterPolicy, FixtureAgentAdapter
    from praxile.eval.v2.candidate import ContextCandidate
    from praxile.eval.v2.development_cycle import DevelopmentContextCycle
    candidates = [ContextCandidate(candidate_id=name, version="1", title=name, candidate_type="context_policy",
        context_item={"content": name, **({"executor_requirements": validate_action_examples([action()], profile_context(profile()))}
                                        if name != "composition" else {})}, confidence=0,
        evidence_refs=("event:e",), expected_effect={"hypothesis": name}) for name in ("a", "b", "composition")]
    with pytest.raises(EvalSchemaError, match="mix"):
        DevelopmentContextCycle(tmp_path, None).run(None, candidates=candidates[:2], composed_candidate=candidates[2],
            development_task_ids=["dev"], heldout_task_ids=["test"], cycle_id="cycle", model={},
            adapter=FixtureAgentAdapter(), evaluator=None, baseline_policy=AdapterPolicy(policy_id="base"))
