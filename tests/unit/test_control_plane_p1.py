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
    PromotionGateEvaluator,
    PromotionThresholds,
    SkillAsset,
    SkillAssetEvaluator,
    SkillCaseResult,
    SkillMarkdownProjector,
    StageBudget,
    SubagentControlService,
    SubagentPolicy,
    validate_delegation_trace,
)
from praxile.trace import AgentEvent
from praxile.adapters import AdapterPolicy, AdapterRunner, AdapterTask, FixtureAgentAdapter
from praxile.config import Config
from praxile.trace import EventStore, RunHandle


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


def test_context_policy_persists_source_utilization_and_compression_trace(tmp_path) -> None:
    policy = ContextPolicy(
        policy_id="measured-context",
        version="1",
        status="active",
        source_rules=(
            ContextSourceRule(
                source="recent_trajectory",
                stages=("exploration",),
                max_tokens=100,
            ),
            ContextSourceRule(
                source="retrieved_skill",
                stages=("implementation",),
                max_tokens=80,
            ),
        ),
        stage_budgets=budgets(),
    )
    compiled = policy.compile(
        (
            {
                "source": "recent_trajectory",
                "content": "A compact trajectory summary",
                "_praxile_measurement": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "compression_profile": "trajectory-compact-v1",
                    "decision": "compressed",
                    "reason": "history crossed the configured context ratio",
                },
            },
        )
    )
    assert "_praxile_measurement" not in compiled.context[0]

    result = AdapterRunner(EventStore(Config.load(tmp_path).paths)).execute(
        FixtureAgentAdapter(),
        AdapterTask("task_context_usage", "Inspect the regression", str(tmp_path)),
        compiled,
    )

    usage = [event for event in result.events if event.type == "CONTEXT_SOURCE_USAGE"]
    assert len(usage) == 2
    trajectory = next(event for event in usage if event.payload["source"] == "recent_trajectory")
    assert trajectory.payload["selected_items"] == 1
    assert trajectory.payload["input_tokens"] == 80
    assert trajectory.payload["output_tokens"] == 20
    assert trajectory.payload["utilization_ratio"] == 0.2
    assert trajectory.payload["compression"][0]["profile"] == "trajectory-compact-v1"
    assert trajectory.payload["compression"][0]["token_measurement"] == "reported"
    skill = next(event for event in usage if event.payload["source"] == "retrieved_skill")
    assert skill.payload["status"] == "not_selected"


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


def test_skill_evaluator_and_markdown_projection_require_all_declared_cases() -> None:
    meta = AssetMeta(
        asset_id="skill_eval",
        type="skill",
        source_evidence=(evidence(),),
        scope={"repository": "example/project"},
        applicable_conditions=("Parser regression",),
        confidence=0.75,
        version="1",
        created_from_run="run_1",
    )
    skill = SkillAsset(
        meta=meta,
        name="Parser repair",
        input_schema={"type": "object"},
        preconditions=("Failure reproduced",),
        context_requirements=("Parser source",),
        allowed_tools=("read_file", "run_test"),
        procedure=("Reproduce", "Repair"),
        verification_contract=("Regression passes",),
        failure_modes=("Tokenizer owns the defect",),
        eval_cases=("case_a", "case_b"),
    )
    partial = SkillAssetEvaluator().evaluate(
        skill,
        {"case_a": SkillCaseResult("case_a", True, (evidence("eval", "eval_case_a"),))},
    )
    assert partial["eligible_for_promotion"] is False
    assert partial["missing"] == ["case_b"]

    complete = SkillAssetEvaluator().evaluate(
        skill,
        {
            "case_a": SkillCaseResult("case_a", True, (evidence("eval", "eval_case_a"),)),
            "case_b": SkillCaseResult("case_b", True, (evidence("eval", "eval_case_b"),)),
        },
    )
    markdown = SkillMarkdownProjector.render(skill, complete)
    assert complete["eligible_for_promotion"] is True
    assert "## Verification Contract" in markdown
    assert "`case_a`" in markdown
    assert '"eligible_for_promotion": true' in markdown


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


def subagent_policy(*, status: str = "active") -> SubagentPolicy:
    return SubagentPolicy(
        policy_id="isolated-verification",
        version="1",
        trigger_conditions=("Independent verification is required",),
        allowed_backends=("fixture",),
        allowed_context_modes=("isolated", "fresh"),
        max_children=2,
        max_parallel=1,
        max_token_budget=4000,
        max_time_budget_seconds=300,
        max_cost_budget=2.0,
        status=status,
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


def test_subagent_control_negotiates_capability_and_records_child_dag(tmp_path) -> None:
    event_store = EventStore(Config.load(tmp_path).paths)
    parent = RunHandle("fixture", "native_parent", "trace_sub", "parent_1", "task_1")
    service = SubagentControlService(event_store)
    result = service.delegate(
        adapter=FixtureAgentAdapter(),
        parent=parent,
        project_root=tmp_path,
        contract=delegation_contract(),
        control_policy=subagent_policy(),
        policy=AdapterPolicy(policy_id="subagent-policy"),
    )
    assert result.child.handle.run_id.startswith("subrun_")
    assert all(event.parent_run_id == parent.run_id for event in result.child.events)
    assert [event.type for event in event_store.list_events(trace_id=parent.trace_id)].count("SUBAGENT_START") == 1
    assert [event.type for event in event_store.list_events(trace_id=parent.trace_id)].count("SUBAGENT_END") == 1


def test_subagent_control_refuses_an_adapter_without_visibility(tmp_path) -> None:
    adapter = FixtureAgentAdapter()
    adapter.capabilities = lambda: replace(adapter.__class__().capabilities(), subagent_visibility=False)  # type: ignore[method-assign]
    service = SubagentControlService(EventStore(Config.load(tmp_path).paths))
    with pytest.raises(ControlPlaneSchemaError, match="does not expose subagent"):
        service.delegate(
            adapter=adapter,
            parent=RunHandle("fixture", "native_parent", "trace_sub", "parent_1", "task_1"),
            project_root=tmp_path,
            contract=delegation_contract(),
            control_policy=subagent_policy(),
            policy=AdapterPolicy(),
        )


def test_subagent_policy_blocks_candidate_activation_and_budget_overrun() -> None:
    with pytest.raises(ControlPlaneSchemaError, match="not active"):
        subagent_policy(status="candidate").authorize(delegation_contract())
    oversized = replace(delegation_contract(), token_budget=5000)
    with pytest.raises(ControlPlaneSchemaError, match="token budget"):
        subagent_policy().authorize(oversized)


def test_subagent_merge_gate_persists_only_cited_verifier_evidence(tmp_path) -> None:
    event_store = EventStore(Config.load(tmp_path).paths)
    verification = AgentEvent.create(
        trace_id="trace_merge",
        run_id="verifier_1",
        parent_run_id="parent_1",
        task_id="task_1",
        type="VERIFICATION",
        actor="independent-verifier",
        payload={"status": "passed"},
    )
    event_store.append(verification)
    service = SubagentControlService(event_store)
    decision = MergeDecision(
        contract_id="delegation_1",
        parent_run_id="parent_1",
        child_run_id="child_1",
        verifier_run_id="verifier_1",
        decision="merge",
        evidence=(EvidenceRef("event", verification.event_id),),
        rationale="Independent verification passed.",
    )
    checkpoint = service.record_merge_decision(decision, task_id="task_1", trace_id="trace_merge")
    assert checkpoint.type == "CHECKPOINT"
    assert checkpoint.payload["checkpoint_type"] == "merge_gate"
    assert checkpoint.evidence_refs == (verification.event_id,)


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


def test_promotion_gate_evaluator_produces_all_six_gates() -> None:
    report = {
        "baseline": {"eval_run_id": "baseline_1"},
        "candidate": {"eval_run_id": "candidate_1"},
        "invariant_check": {"valid": True},
        "comparison": {
            "decision": "improve",
            "totals": {"regressions": 0, "cost_delta": 0.05},
            "task_results": [{"diff_scope": {"candidate_status": "passed", "passed": True}}],
        },
    }
    result = PromotionGateEvaluator().evaluate(
        candidate(),
        report,
        reviewer="maintainer",
        human_approved=True,
        thresholds=PromotionThresholds(max_cost_increase=0.1),
    )
    assert result.decision == "promote"
    assert {gate.gate for gate in result.gates} == {"evidence", "quality", "regression", "cost", "human", "rollback"}


def test_promotion_gate_evaluator_abstains_without_human_approval() -> None:
    report = {
        "baseline": {"eval_run_id": "baseline_1"},
        "candidate": {"eval_run_id": "candidate_1"},
        "invariant_check": {"valid": True},
        "comparison": {
            "decision": "improve",
            "totals": {"regressions": 0, "cost_delta": 0.0},
            "task_results": [{"diff_scope": {"candidate_status": "passed", "passed": True}}],
        },
    }
    result = PromotionGateEvaluator().evaluate(candidate(), report, reviewer="maintainer", human_approved=False)
    assert result.decision == "abstain"
    assert next(gate for gate in result.gates if gate.gate == "human").passed is False


def test_quality_tie_never_promotes_unknown_objective_results() -> None:
    report = {
        "baseline": {"eval_run_id": "baseline_1"},
        "candidate": {"eval_run_id": "candidate_1"},
        "invariant_check": {"valid": True},
        "comparison": {
            "decision": "inconclusive",
            "totals": {"regressions": 0, "cost_delta": 0.0},
            "task_results": [
                {
                    "task_id": "task_1",
                    "transition": "unknown",
                    "diff_scope": {"candidate_status": "passed", "passed": True},
                }
            ],
        },
    }
    result = PromotionGateEvaluator().evaluate(
        candidate(),
        report,
        reviewer="maintainer",
        human_approved=True,
        thresholds=PromotionThresholds(require_quality_improvement=False),
    )
    assert result.decision == "abstain"
    assert next(gate for gate in result.gates if gate.gate == "quality").passed is False


def test_diff_scope_review_blocks_quality_gate_even_when_ab_improves() -> None:
    report = {
        "baseline": {"eval_run_id": "baseline_1"},
        "candidate": {"eval_run_id": "candidate_1"},
        "invariant_check": {"valid": True},
        "comparison": {
            "decision": "improve",
            "totals": {"regressions": 0, "cost_delta": 0.0},
            "task_results": [
                {
                    "task_id": "task_1",
                    "transition": "unchanged",
                    "diff_scope": {"candidate_status": "review_required", "passed": False},
                }
            ],
        },
    }

    result = PromotionGateEvaluator().evaluate(
        candidate(), report, reviewer="maintainer", human_approved=True
    )

    quality = next(gate for gate in result.gates if gate.gate == "quality")
    assert quality.passed is False
    assert "diff_scope_passed=False" in quality.summary
    assert result.decision == "reject"
