from __future__ import annotations

from praxile.control_plane import (
    AssetMeta,
    ControlledArmMeasurement,
    EvidenceRef,
    SkillActivationExperiment,
    SkillAsset,
    SubagentComparisonExperiment,
)
from praxile.trace import AgentEvent


def _arm(arm_id: str, *, resolved: int, tokens: int) -> ControlledArmMeasurement:
    return ControlledArmMeasurement(
        arm_id=arm_id,
        task_set_digest="sha256:fixture-task-set",
        adapter="fixture",
        model="fixture-model-v1",
        evaluator="fixture-evaluator-v1",
        task_count=2,
        resolved_count=resolved,
        input_tokens=tokens,
        output_tokens=100,
        tool_calls=10,
        latency_ms=1000,
        cost=0.1,
    )


def _skill() -> SkillAsset:
    return SkillAsset(
        meta=AssetMeta(
            asset_id="skill_focused_parser",
            type="skill",
            source_evidence=(EvidenceRef("event", "source_event"),),
            scope={"repository": "fixture/project"},
            applicable_conditions=("A parser regression is reproduced",),
            confidence=0.8,
            version="1",
            created_from_run="source_run",
        ),
        name="Focused parser repair",
        input_schema={"type": "object"},
        preconditions=("The parser failure is reproducible",),
        context_requirements=("Failing test and parser source",),
        allowed_tools=("read_file", "run_test", "edit_file"),
        procedure=("Reproduce the focused failure", "Patch the narrowest branch"),
        verification_contract=("Focused and regression tests pass",),
        failure_modes=("The failure belongs to tokenization",),
        eval_cases=("fixture_parser_case",),
    )


def _event(event_id: str, event_type: str, payload: dict) -> AgentEvent:
    return AgentEvent.create(
        event_id=event_id,
        trace_id="trace_candidate",
        run_id="run_candidate",
        task_id="task_fixture",
        type=event_type,
        actor="fixture-agent",
        payload=payload,
    )


def test_skill_activation_experiment_separates_delivery_reference_and_credit() -> None:
    skill = _skill()
    events = (
        _event(
            "event_inject",
            "CONTEXT_INJECT",
            {"items": [{"asset_id": skill.meta.asset_id, "asset_version": "1"}]},
        ),
        _event(
            "event_reference",
            "SKILL_REFERENCE",
            {"asset_id": skill.meta.asset_id, "asset_version": "1"},
        ),
    )

    result = SkillActivationExperiment().evaluate(
        skill,
        _arm("baseline", resolved=0, tokens=1000),
        _arm("candidate", resolved=1, tokens=800),
        events,
    )

    assert result["invariant_check"]["valid"] is True
    assert result["activation"] == {
        "injected": True,
        "referenced": True,
        "injection_event_ids": ["event_inject"],
        "reference_event_ids": ["event_reference"],
    }
    assert result["comparison"]["decision"] == "improve"
    assert result["attribution"]["status"] == "credited"

    abstained = SkillActivationExperiment().evaluate(
        skill,
        _arm("baseline", resolved=0, tokens=1000),
        _arm("candidate", resolved=1, tokens=800),
        events[:1],
    )
    assert abstained["attribution"]["status"] == "abstained"


def test_subagent_comparison_requires_complete_delegation_and_merge_evidence() -> None:
    events = (
        _event(
            "event_subagent_start",
            "SUBAGENT_START",
            {"contract_id": "delegation_1", "child_run_id": "child_1"},
        ),
        _event(
            "event_subagent_end",
            "SUBAGENT_END",
            {"contract_id": "delegation_1", "child_run_id": "child_1", "status": "completed"},
        ),
        _event(
            "event_merge",
            "CHECKPOINT",
            {
                "checkpoint_type": "merge_gate",
                "decision": "merge",
                "contract_id": "delegation_1",
                "child_run_id": "child_1",
                "verifier_run_id": "verifier_1",
            },
        ),
    )

    result = SubagentComparisonExperiment().evaluate(
        _arm("no-subagent", resolved=0, tokens=1000),
        _arm("subagent", resolved=1, tokens=900),
        events,
    )

    assert result["delegation"]["trace_complete"] is True
    assert result["comparison"]["decision"] == "improve"
    assert result["attribution"]["status"] == "credited"

    abstained = SubagentComparisonExperiment().evaluate(
        _arm("no-subagent", resolved=0, tokens=1000),
        _arm("subagent", resolved=1, tokens=900),
        events[:2],
    )
    assert abstained["attribution"]["status"] == "abstained"
