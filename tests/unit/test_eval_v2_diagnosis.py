from __future__ import annotations

import pytest

from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.diagnosis import (
    CausalAttribution,
    FailureDiagnoser,
    FailureDiagnosis,
)
from praxile.adapters import AdapterPolicy
from praxile.eval.v2 import EvalSchemaError
from praxile.trace import AgentEvent


def _event(event_id: str, event_type: str, payload: dict) -> AgentEvent:
    return AgentEvent.create(
        event_id=event_id,
        timestamp="2026-01-01T00:00:00+00:00",
        trace_id="trace-1",
        run_id="run-1",
        task_id="task-1",
        type=event_type,
        actor="fixture",
        payload=payload,
    )


def _result(*, resolved: bool | None, status: str = "completed") -> dict:
    return {
        "task_id": "task-1",
        "trace_id": "trace-1",
        "agent_run_id": "run-1",
        "status": status,
        "evaluator": {"resolved": resolved},
    }


def test_diagnosis_round_trip_attributes_single_direct_category() -> None:
    events = [
        _event("event-start", "RUN_START", {}),
        _event("event-verify", "VERIFICATION", {"status": "failed", "resolved": False}),
        _event("event-end", "RUN_END", {"status": "completed"}),
    ]
    diagnosis = FailureDiagnoser().diagnose(_result(resolved=False), events)

    assert diagnosis.outcome == "failure"
    assert diagnosis.attribution.category == "VERIFICATION"
    assert diagnosis.attribution.abstained is False
    assert diagnosis.attribution.evidence_refs == ("event:event-verify",)
    assert FailureDiagnosis.from_dict(diagnosis.to_dict()) == diagnosis
    assert FailureDiagnoser().diagnose(_result(resolved=False), events).diagnosis_id == diagnosis.diagnosis_id


def test_diagnosis_abstains_when_detected_categories_conflict() -> None:
    diagnosis = FailureDiagnoser().diagnose(
        _result(resolved=False),
        [
            _event("event-model", "MODEL_CALL", {"parse_status": "rejected"}),
            _event("event-tool", "TOOL_RESULT", {"status": "failed", "returncode": 2}),
        ],
    )

    assert {item.category for item in diagnosis.detections} == {"MODEL", "TOOL"}
    assert diagnosis.attribution == CausalAttribution(
        category="UNKNOWN",
        confidence=0.0,
        abstained=True,
        rationale="Multiple observed categories prevent a defensible single-cause attribution.",
        evidence_refs=("event:event-model", "event:event-tool"),
    )


def test_successful_task_never_receives_failure_cause() -> None:
    diagnosis = FailureDiagnoser().diagnose(
        _result(resolved=True),
        [_event("event-tool", "TOOL_RESULT", {"status": "failed", "returncode": 1})],
    )

    assert diagnosis.outcome == "no_failure"
    assert diagnosis.attribution.category == "UNKNOWN"
    assert diagnosis.attribution.abstained is True


def test_native_budget_exhaustion_is_policy_harness_evidence() -> None:
    diagnosis = FailureDiagnoser().diagnose(
        _result(resolved=None, status="error"),
        [
            _event(
                "event-final",
                "FINAL_RESULT",
                {"status": "failed", "native_exit_status": "LimitsExceeded"},
            ),
            _event("event-end", "RUN_END", {"status": "failed"}),
        ],
    )

    assert diagnosis.attribution.category == "POLICY_HARNESS"
    assert diagnosis.attribution.abstained is False
    assert diagnosis.detections[0].code == "execution_budget_exhausted"


def test_context_candidate_is_exactly_one_item_and_preserves_invariants() -> None:
    candidate = ContextCandidate(
        candidate_id="bounded-investigation",
        version="1",
        title="Bound broad repository exploration",
        candidate_type="experience_activation",
        context_item={"asset_id": "pattern-1", "content": "Start with the introducing commit and one focused test."},
        confidence=0.7,
        evidence_refs=("event:event-training",),
        expected_effect={"tool_calls": "decrease"},
        source_diagnosis_ids=("diagnosis-1",),
        source_task_ids=("train-task",),
    )
    baseline = AdapterPolicy(
        policy_id="p0-baseline",
        version="1",
        budgets={"wall_timeout_seconds": 60},
        settings={"step_limit": 50, "workspace_isolated": True},
    )

    policy = candidate.policy(baseline)

    assert len(policy.context) == 1
    assert policy.budgets == baseline.budgets
    assert policy.settings == baseline.settings
    assert policy.context[0]["candidate_digest"] == candidate.digest
    candidate.validate_clean_track({"eval-task"})


def test_context_candidate_rejects_clean_track_leakage() -> None:
    candidate = ContextCandidate(
        candidate_id="candidate-1",
        version="1",
        title="Candidate",
        candidate_type="context_policy",
        context_item={"content": "Use a focused test."},
        confidence=0.6,
        evidence_refs=("event:event-training",),
        expected_effect={"tool_calls": "decrease"},
        source_task_ids=("held-out-task",),
    )

    with pytest.raises(EvalSchemaError, match="overlap"):
        candidate.validate_clean_track({"held-out-task"})


def test_context_candidate_rejects_non_object_expected_effect() -> None:
    with pytest.raises(EvalSchemaError, match="expected_effect must be objects"):
        ContextCandidate.from_dict(
            {
                "schema_version": "praxile.context_candidate.v1",
                "candidate_id": "candidate-1",
                "version": "1",
                "title": "Candidate",
                "candidate_type": "context_policy",
                "context_item": {"content": "Use a focused test."},
                "confidence": 0.6,
                "evidence_refs": ["event:event-training"],
                "expected_effect": ["decrease tool calls"],
            }
        )
