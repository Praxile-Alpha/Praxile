from __future__ import annotations

import pytest

from praxile.adapters import AdapterPolicy
from praxile.eval.v2 import ContextActivationGate, ContextCandidate, EvalSchemaError, EvalTask, ExperienceRepresentationRouter, RepositorySpec, SWEbenchEvaluationSpec
from praxile.eval.v2.activation import resolve_task_policy


def _candidate(*, density: float = 0.9, budget: int = 100) -> ContextCandidate:
    return ContextCandidate(
        candidate_id="representation-fixture", version="1", title="Represent experience",
        candidate_type="experience_activation", context_item={"content": "Legacy context"},
        confidence=0.9, evidence_refs=("event:source",), expected_effect={"tokens": "decrease"},
        applies_to={"repositories": ["owner/repo"], "task_signals": ["parser"]},
        representation_options={
            "raw_episode": "Raw episode with exact reproduction and command output.",
            "summary_memory": "Short project memory.",
            "skill": "Reusable skill steps.",
            "failure_pattern": "Failure signature and repair.",
        },
        representation_profile={"compressibility": 0.3, "evidence_density": density, "token_budget": budget},
    )


def _task(task_id: str, *, state: float, intent: str = "neutral", instruction: str = "Fix parser", budget: int | None = None, compressibility: float | None = None) -> EvalTask:
    metadata = {"context_state_dependency": state, "experience_intent": intent}
    if compressibility is not None:
        metadata["context_compressibility"] = compressibility
    if budget is not None:
        metadata["context_token_budget"] = budget
    return EvalTask(task_id, instruction, RepositorySpec("owner/repo", "abc123", "https://example.invalid/repo.git"),
                    SWEbenchEvaluationSpec("fixture", "test"), metadata=metadata)


def test_representation_router_selects_all_forms_and_none_after_activation() -> None:
    candidate = _candidate()
    tasks = [
        _task("raw", state=1),
        _task("summary", state=0.5, compressibility=0.95),
        _task("skill", state=0, intent="procedure"),
        _task("failure", state=0.5, intent="failure"),
        _task("outside", state=1, instruction="Update documentation"),
    ]
    activation = ContextActivationGate().plan(candidate, tasks)
    assert activation["decisions"]["outside"]["activated"] is False
    plan = ExperienceRepresentationRouter().plan(candidate, tasks, activation)
    selected = {task_id: row["selected"] for task_id, row in plan["decisions"].items()}
    assert selected == {"raw": "raw_episode", "summary": "summary_memory", "skill": "skill", "failure": "failure_pattern", "outside": "none"}
    assert plan["selected_count"] == 4
    policy = candidate.policy(AdapterPolicy(policy_id="baseline"), activation_plan=activation, representation_plan=plan)
    effective, record = resolve_task_policy(policy, "raw")
    assert effective.context[0]["content"] == candidate.representation_options["raw_episode"]
    assert "representation_options" not in effective.context[0]
    assert record["decisions"][0]["representation"]["selected"] == "raw_episode"
    effective, record = resolve_task_policy(policy, "outside")
    assert not effective.context
    assert record["status"] == "abstained"


def test_representation_router_abstains_on_low_evidence_or_budget() -> None:
    task = _task("task", state=1)
    for candidate in (_candidate(density=0.2), _candidate(budget=1)):
        activation = ContextActivationGate().plan(candidate, [task])
        plan = ExperienceRepresentationRouter().plan(candidate, [task], activation)
        assert plan["decisions"]["task"]["selected"] == "none"
        effective, record = resolve_task_policy(
            candidate.policy(AdapterPolicy(policy_id="baseline"), activation_plan=activation, representation_plan=plan), "task"
        )
        assert not effective.context
        assert record["activated"] is True
        assert record["injected_context_items"] == 0


def test_representation_schema_and_legacy_candidate_are_backward_compatible() -> None:
    legacy = ContextCandidate(
        candidate_id="legacy", version="1", title="Legacy", candidate_type="experience_activation",
        context_item={"content": "Legacy text"}, confidence=0.8, evidence_refs=("event:source",),
        expected_effect={"tokens": "decrease"},
    )
    assert "representation_options" not in legacy.to_dict()
    assert ContextCandidate.from_dict(legacy.to_dict()).digest == legacy.digest
    with pytest.raises(EvalSchemaError, match="representation_profile requires"):
        ContextCandidate.from_dict({**legacy.to_dict(), "representation_profile": {"token_budget": 10}})
    with pytest.raises(EvalSchemaError, match="unsupported experience representation"):
        ContextCandidate.from_dict({**legacy.to_dict(), "representation_options": {"answer": "secret"},
                                    "representation_profile": {"compressibility": 0.5, "evidence_density": 0.9, "token_budget": 10}})
