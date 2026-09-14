from __future__ import annotations

from praxile.adapters import AdapterPolicy
from praxile.eval.v2 import (
    ContextActivationGate,
    ContextCandidate,
    EvalTask,
    RepositorySpec,
    SWEbenchEvaluationSpec,
    resolve_task_policy,
)


def _candidate(**applies_to: object) -> ContextCandidate:
    return ContextCandidate(
        candidate_id="bounded-investigation",
        version="1",
        title="Bound investigation",
        candidate_type="experience_activation",
        context_item={"content": "Start from the version boundary and shared base class."},
        confidence=0.7,
        evidence_refs=("event:event-training",),
        expected_effect={"tool_calls": "decrease"},
        source_task_ids=("training-task",),
        applies_to=applies_to,
    )


def _task(instruction: str, repo: str = "sympy/sympy") -> EvalTask:
    return EvalTask(
        task_id="held-out-task",
        instruction=instruction,
        repository=RepositorySpec(repo, "abc123", f"https://github.com/{repo}.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )


def test_repository_and_semantic_signal_must_both_match() -> None:
    candidate = _candidate(
        repositories=["sympy/sympy"],
        task_signals=["shared inheritance", "regression across versions"],
    )

    matched = ContextActivationGate().evaluate(
        candidate, _task("A shared inheritance regression breaks subclasses")
    )
    unrelated = ContextActivationGate().evaluate(
        candidate, _task("Improve Product pretty printing")
    )
    wrong_repo = ContextActivationGate().evaluate(
        candidate,
        _task("A shared inheritance regression breaks subclasses", "django/django"),
    )

    assert matched.activated is True
    assert matched.matched_signals[0]["signal"] == "shared inheritance"
    assert unrelated.activated is False
    assert "no task signal" in unrelated.reasons[0]
    assert wrong_repo.activated is False
    assert "repository" in wrong_repo.reasons[0]


def test_anti_scope_overrides_positive_signal() -> None:
    candidate = _candidate(
        repositories=["sympy/sympy"],
        task_signals=["shared inheritance"],
        does_not_apply_when=["failure already isolated to leaf implementation"],
    )

    decision = ContextActivationGate().evaluate(
        candidate,
        _task(
            "Shared inheritance is involved, but the failure is already isolated to leaf implementation"
        ),
    )

    assert decision.activated is False
    assert decision.matched_anti_scopes
    assert "does_not_apply_when" in decision.reasons[-1]


def test_activation_plan_controls_effective_task_policy() -> None:
    candidate = _candidate(repositories=["sympy/sympy"], task_signals=["shared inheritance"])
    matching = _task("Fix a shared inheritance failure")
    unrelated = EvalTask(
        task_id="unrelated-task",
        instruction="Improve pretty printing",
        repository=matching.repository,
        evaluation=matching.evaluation,
    )
    plan = ContextActivationGate().plan(candidate, (matching, unrelated))
    policy = candidate.policy(AdapterPolicy(policy_id="baseline"), activation_plan=plan)

    active_policy, active_evidence = resolve_task_policy(policy, matching.task_id)
    abstained_policy, abstained_evidence = resolve_task_policy(policy, unrelated.task_id)

    assert len(active_policy.context) == 1
    assert "activation_gate" not in active_policy.context[0]
    assert active_evidence["status"] == "activated"
    assert abstained_policy.context == ()
    assert abstained_evidence["status"] == "abstained"
    assert plan["activated_count"] == 1
    assert plan["abstained_count"] == 1
