from __future__ import annotations

from dataclasses import replace

import pytest

from praxile.control_plane import (
    AssetMeta,
    CandidateEvaluation,
    ContextPolicy,
    ContextSourceRule,
    ControlPlaneSchemaError,
    DelegationContract,
    EvidenceRef,
    GateResult,
    HarnessCandidate,
    HarnessEvolutionRegistry,
    MergeDecision,
    SkillAsset,
    StageBudget,
    validate_delegation_trace,
)
from praxile.trace import AgentEvent


def evidence(kind: str = "event", ref_id: str = "event_1") -> EvidenceRef:
    return EvidenceRef(type=kind, ref_id=ref_id, claim="Observed by the normalized trace")


def budgets() -> tuple[StageBudget, ...]:
    return tuple(StageBudget(stage=stage, token_limit=1000, tool_call_limit=10, time_limit_seconds=60) for stage in ("exploration", "implementation", "verification"))


def context_policy(*, status: str = "candidate") -> ContextPolicy:
    return ContextPolicy(
        policy_id="focused-context",
        version="2",
        status=status,
        source_rules=(ContextSourceRule(source="task_spec", stages=("exploration", "implementation", "verification"), required=True),),
        stage_budgets=budgets(),
        retrieval={"top_k": 4, "strategy": "hybrid"},
        history={"mode": "compact", "compact_at_ratio": 0.8},
        repository={"mode": "search_on_demand"},
        experience={"include_counterexamples": True},
    )


def candidate(candidate_id: str = "candidate_1", version: str = "2", base_version: str = "1") -> HarnessCandidate:
    return HarnessCandidate(
        candidate_id=candidate_id,
        type="context_policy",
        component_key="context.default",
        base_version=base_version,
        candidate_version=version,
        hypothesis="Focused context reduces duplicate exploration without lowering success.",
        source_evidence=(evidence(),),
        payload=context_policy().to_dict(),
    )


def evaluation(candidate_id: str = "candidate_1", base_version: str = "1") -> CandidateEvaluation:
    return CandidateEvaluation(
        candidate_id=candidate_id,
        baseline_ref="eval:baseline_1",
        candidate_eval_ref="eval:candidate_1",
        gates=tuple(GateResult(gate=name, passed=True, evidence=(evidence("eval", f"eval_{name}"),), summary=f"{name} passed") for name in ("evidence", "quality", "regression", "cost", "human", "rollback")),
        decision="promote",
        reviewer="human:maintainer",
        metrics={"success_delta": 0.1, "token_delta": -100},
        rollback_target={"component_key": "context.default", "version": base_version},
    )


def test_context_policy_round_trip_and_evaluation_only_compilation() -> None:
    policy = context_policy()
    restored = ContextPolicy.from_dict(policy.to_dict())

    with pytest.raises(ControlPlaneSchemaError, match="explicitly marked evaluation"):
        restored.compile(({"source": "task_spec", "content": "Fix the parser"},))

    compiled = restored.compile(({"source": "task_spec", "content": "Fix the parser"},), evaluation=True)
    assert compiled.settings["evaluation_only"] is True
    assert compiled.budgets["stages"]["exploration"]["token_limit"] == 1000

    production = replace(restored, status="active").compile(({"source": "task_spec", "content": "Fix"},))
    assert production.settings["evaluation_only"] is False


def test_context_policy_rejects_undeclared_sources_and_incomplete_stage_budgets() -> None:
    with pytest.raises(ControlPlaneSchemaError, match="every stage"):
        ContextPolicy(
            policy_id="bad",
            version="1",
            source_rules=(ContextSourceRule(source="task_spec", stages=("exploration",)),),
            stage_budgets=budgets()[:2],
        )
    with pytest.raises(ControlPlaneSchemaError, match="undeclared source"):
        context_policy(status="active").compile(({"source": "retrieved_skill", "content": "Hidden"},))
    invalid = context_policy().to_dict()
    invalid["source_rules"][0]["required"] = "false"
    with pytest.raises(ControlPlaneSchemaError, match="must be a boolean"):
        ContextPolicy.from_dict(invalid)


def test_skill_asset_is_first_class_and_cannot_activate_itself() -> None:
    meta = AssetMeta(
        asset_id="skill_parser",
        type="skill",
        source_evidence=(evidence(),),
        scope={"repository": "example/project"},
        applicable_conditions=("Parser regression in this repository",),
        confidence=0.8,
        version="1",
        created_from_run="run_1",
    )
    skill = SkillAsset(
        meta=meta,
        name="Focused parser repair",
        input_schema={"type": "object", "properties": {"failure": {"type": "string"}}},
        preconditions=("A focused parser failure is reproducible",),
        context_requirements=("Failing test and parser source",),
        allowed_tools=("read_file", "run_test", "edit_file"),
        procedure=("Reproduce the smallest failing case", "Patch the narrowest parser branch"),
        verification_contract=("Focused test passes", "Regression suite passes"),
        failure_modes=("The failure originates in tokenization rather than parsing",),
        eval_cases=("eval_case_parser_1",),
    )
    assert SkillAsset.from_dict(skill.to_dict()) == skill
    with pytest.raises(ControlPlaneSchemaError, match="only active skills"):
        skill.context_item()

    active_meta = replace(
        meta,
        status="active",
        reviewer="human:maintainer",
        eval_result={"decision": "promote", "eval_run_id": "eval_1"},
        activation_history=({"action": "promote", "at": "2026-09-13T00:00:00+00:00"},),
    )
    item = replace(skill, meta=active_meta).context_item()
    assert item["asset_id"] == "skill_parser"
    assert item["verification_contract"] == ["Focused test passes", "Regression suite passes"]


def test_skill_asset_requires_verification_and_eval_cases() -> None:
    meta = AssetMeta(
        asset_id="skill_bad",
        type="skill",
        source_evidence=(evidence(),),
        scope={"repository": "example/project"},
        applicable_conditions=("Always",),
        confidence=0.5,
        version="1",
        created_from_run="run_1",
    )
    with pytest.raises(ControlPlaneSchemaError, match="verification_contract"):
        SkillAsset(
            meta=meta,
            name="Bad skill",
            input_schema={"type": "object"},
            preconditions=("Condition",),
            context_requirements=("Context",),
            allowed_tools=("read_file",),
            procedure=("Read",),
            verification_contract=(),
            failure_modes=("Unknown",),
            eval_cases=("eval_1",),
        )


def delegation_contract() -> DelegationContract:
    return DelegationContract(
        contract_id="delegation_1",
        objective="Independently verify the proposed parser fix.",
        scope={"paths": ["src/parser.py", "tests/test_parser.py"]},
        input_artifact_ids=("patch_1",),
        context_mode="isolated",
        allowed_tools=("read_file", "run_test"),
        backend="fixture",
        model="verifier-model",
        token_budget=2000,
        time_budget_seconds=120,
        cost_budget=1.0,
        termination_criteria=("Verification result is conclusive",),
        verification_criteria=("Focused and regression tests pass",),
        return_schema={"type": "object", "required": ["verified"]},
    )


def test_delegation_contract_merge_gate_and_trace_dag() -> None:
    contract = delegation_contract()
    events = [
        AgentEvent.create(trace_id="trace_1", run_id="parent_1", task_id="task_1", type="SUBAGENT_START", actor="parent", payload={"contract_id": contract.contract_id, "child_run_id": "child_1"}),
        AgentEvent.create(trace_id="trace_1", run_id="child_1", parent_run_id="parent_1", task_id="task_1", type="RUN_START", actor="child", payload={}),
        AgentEvent.create(trace_id="trace_1", run_id="child_1", parent_run_id="parent_1", task_id="task_1", type="VERIFICATION", actor="child", payload={"passed": True}),
        AgentEvent.create(trace_id="trace_1", run_id="child_1", parent_run_id="parent_1", task_id="task_1", type="RUN_END", actor="child", payload={"status": "completed"}),
        AgentEvent.create(trace_id="trace_1", run_id="parent_1", task_id="task_1", type="SUBAGENT_END", actor="parent", payload={"contract_id": contract.contract_id, "child_run_id": "child_1"}),
    ]
    validate_delegation_trace(events, contract, parent_run_id="parent_1", child_run_id="child_1")
    decision = MergeDecision(
        contract_id=contract.contract_id,
        parent_run_id="parent_1",
        child_run_id="child_1",
        verifier_run_id="verifier_1",
        decision="merge",
        evidence=(evidence("event", events[2].event_id),),
        rationale="Independent verifier reproduced the expected result.",
    )
    assert MergeDecision.from_dict(decision.to_dict()) == decision


def test_merge_gate_requires_an_isolated_verifier() -> None:
    with pytest.raises(ControlPlaneSchemaError, match="must be isolated"):
        MergeDecision(
            contract_id="delegation_1",
            parent_run_id="parent_1",
            child_run_id="child_1",
            verifier_run_id="child_1",
            decision="merge",
            evidence=(evidence(),),
            rationale="Self verified",
        )


def test_registry_requires_six_gates_and_supports_atomic_rollback(tmp_path) -> None:
    registry = HarnessEvolutionRegistry(tmp_path)
    first = candidate()
    registry.register(first)
    registry.record_evaluation(evaluation())
    registry.promote(first.candidate_id, approved_by="maintainer@example.com")
    assert registry.snapshot()["active"]["context.default"]["version"] == "2"

    second = candidate("candidate_2", "3", "2")
    registry.register(second)
    registry.record_evaluation(evaluation("candidate_2", "2"))
    registry.promote(second.candidate_id, approved_by="maintainer@example.com")
    assert registry.snapshot()["active"]["context.default"]["version"] == "3"

    registry.rollback("context.default", approved_by="maintainer@example.com")
    state = registry.snapshot()
    assert state["active"]["context.default"]["version"] == "2"
    assert state["candidates"]["candidate_2"]["status"] == "rolled_back"


def test_registry_rejects_a_rollback_target_for_another_component(tmp_path) -> None:
    registry = HarnessEvolutionRegistry(tmp_path)
    registry.register(candidate())
    wrong = replace(evaluation(), rollback_target={"component_key": "skills.default", "version": "1"})
    with pytest.raises(ControlPlaneSchemaError, match="candidate component"):
        registry.record_evaluation(wrong)


def test_registry_rejects_promotion_from_a_stale_base_version(tmp_path) -> None:
    registry = HarnessEvolutionRegistry(tmp_path)
    registry.register(candidate())
    registry.record_evaluation(evaluation())
    registry.promote("candidate_1", approved_by="maintainer@example.com")

    stale = candidate("candidate_stale", "3", "1")
    registry.register(stale)
    registry.record_evaluation(evaluation("candidate_stale", "1"))
    with pytest.raises(ControlPlaneSchemaError, match="stale candidate"):
        registry.promote("candidate_stale", approved_by="maintainer@example.com")


def test_promotion_is_rejected_when_any_gate_fails() -> None:
    gates = tuple(
        GateResult(gate=name, passed=name != "regression", evidence=(evidence(),), summary=name)
        for name in ("evidence", "quality", "regression", "cost", "human", "rollback")
    )
    with pytest.raises(ControlPlaneSchemaError, match="all gates"):
        CandidateEvaluation(
            candidate_id="candidate_1",
            baseline_ref="eval:baseline",
            candidate_eval_ref="eval:candidate",
            gates=gates,
            decision="promote",
            reviewer="human:maintainer",
            rollback_target={"version": "1"},
        )
