from types import SimpleNamespace

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter
from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.development_cycle import DevelopmentContextCycle
from praxile.eval.v2.schema import EvalSchemaError


@pytest.mark.parametrize("composition_verdict,confounders,decision", [
    ("helped", [], "TASK_LOCAL_TRIAL_RECOMMENDED"),
    ("hurt", [], "KEEP_BASE"),
    ("no_observed_effect", [], "KEEP_BASE"),
    ("helped", ["exposure missing"], "KEEP_BASE"),
])
def test_composition_never_inherits_success_from_individuals(tmp_path, composition_verdict, confounders, decision):
    cycle = DevelopmentContextCycle(tmp_path, None)
    calls = []
    def run(tasks, **kwargs):
        calls.append(kwargs)
        composed = len(calls) == 3
        return {"experiment_id": kwargs["experiment_id"], "candidate_digest": kwargs["candidate"].digest,
                "invariant_check": {"valid": True}, "comparison": {"decision": "improve"},
                "improvement_episodes": [{"task_id": "dev", "verdict": composition_verdict if composed else "helped",
                                          "confounders": confounders if composed else []}]}
    cycle.experiments = SimpleNamespace(run=run)
    candidates = [ContextCandidate(candidate_id=name, version="1", title=name,
        candidate_type="context_policy", context_item={"content": name}, confidence=0.5,
        evidence_refs=("event:dev",), source_task_ids=("dev",), expected_effect={"hypothesis": name})
        for name in ("one", "two", "combined")]
    kwargs = dict(candidates=candidates[:2], composed_candidate=candidates[2],
        development_task_ids=["dev"], heldout_task_ids=["sealed"], cycle_id="cycle",
        adapter=FixtureAgentAdapter(), evaluator=SimpleNamespace(identity=lambda: {"name": "fixture"}),
        baseline_policy=AdapterPolicy(policy_id="base"), model={"model": "fixture"})
    tasks = SimpleNamespace(tasks=[SimpleNamespace(task_id="dev")], digest="fixture")
    report = cycle.run(tasks, **kwargs)
    assert len(calls) == 3 and all(call["development_only"] for call in calls)
    assert report["decision"] == decision
    assert report["adopted"] is False and report["promotion_eligible"] is False
    with pytest.raises(EvalSchemaError, match="different content"):
        cycle.run(tasks, resume=True, **{**kwargs, "model": {"model": "changed"}})
    assert len(calls) == 3


@pytest.mark.parametrize("interrupted", [False, True])
def test_composition_resume_never_repeats_an_uncertain_call(tmp_path, interrupted):
    cycle = DevelopmentContextCycle(tmp_path, None)
    cycle.experiments = SimpleNamespace(run=lambda tasks, **kwargs: {
        "experiment_id": kwargs["experiment_id"], "invariant_check": {"valid": True},
        "candidate_digest": kwargs["candidate"].digest,
        "comparison": {}, "improvement_episodes": []})
    parents = [ContextCandidate(candidate_id=name, version="1", title=name,
        candidate_type="context_policy", context_item={"content": name}, confidence=0,
        evidence_refs=("event:dev",), source_task_ids=("dev",), expected_effect={"hypothesis": name})
        for name in ("one", "two")]
    calls = []
    def composer(*args):
        calls.append(args)
        if interrupted:
            raise KeyboardInterrupt()
        return None
    kwargs = dict(candidates=parents, composer=composer, composer_identity={"implementation": "v2"},
        development_task_ids=["dev"], heldout_task_ids=["sealed"], cycle_id="cycle",
        adapter=FixtureAgentAdapter(), evaluator=SimpleNamespace(identity=lambda: {"name": "fixture"}),
        baseline_policy=AdapterPolicy(policy_id="base"), model={"model": "fixture"})
    tasks = SimpleNamespace(tasks=[SimpleNamespace(task_id="dev")], digest="fixture")
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            cycle.run(tasks, **kwargs)
        with pytest.raises(EvalSchemaError, match="automatic replay refused"):
            cycle.run(tasks, **kwargs, resume=True)
    else:
        assert cycle.run(tasks, **kwargs)["composition_status"] == "abstained"
        assert cycle.run(tasks, **kwargs, resume=True)["composition_status"] == "abstained"
    assert len(calls) == 1
    with pytest.raises(EvalSchemaError, match="different content"):
        cycle.run(tasks, **{**kwargs, "composer_identity": {"implementation": "v3"}}, resume=True)
    assert len(calls) == 1
