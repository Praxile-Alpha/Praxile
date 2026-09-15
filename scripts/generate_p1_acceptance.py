from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from praxile.adapters import AdapterPolicy, AdapterRunner, AdapterTask, FixtureAgentAdapter
from praxile.config import Config
from praxile.control_plane import (
    AssetMeta,
    CandidateEvaluation,
    ContextPolicy,
    ContextSourceRule,
    ControlledArmMeasurement,
    EvidenceRef,
    GateResult,
    HarnessCandidate,
    HarnessEvolutionRegistry,
    SkillActivationExperiment,
    SkillAsset,
    StageBudget,
    SubagentComparisonExperiment,
)
from praxile.trace import AgentEvent, EventStore


DEFAULT_OUTPUT = Path("experiments/control_plane/P1_FIXTURE_ACCEPTANCE_V1")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic P1 control-plane acceptance evidence")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    context_evidence = _context_evidence()
    skill, skill_evidence = _skill_evidence()
    subagent_evidence = _subagent_evidence()
    lifecycle_evidence = _promotion_evidence(skill)
    manifest = {
        "schema_version": "praxile.p1_acceptance_manifest.v1",
        "acceptance_id": "p1-control-plane-fixture-v1",
        "execution_mode": "deterministic_fixture",
        "claims": [
            "context source utilization and compression decisions are persisted as trace events",
            "skill delivery, reference, outcome comparison, and causal attribution are distinct",
            "subagent comparison requires delegation and isolated merge evidence",
            "a six-gate candidate can be promoted and atomically rolled back",
        ],
        "non_claims": [
            "This package does not claim real-model task-quality improvement.",
            "This package does not claim mini-SWE-agent native subagent support.",
        ],
        "artifacts": [
            "context-source-trace.json",
            "skill-activation-ab.json",
            "subagent-ab.json",
            "promotion-rollback.json",
        ],
    }
    for name, value in (
        ("manifest.json", manifest),
        ("context-source-trace.json", context_evidence),
        ("skill-activation-ab.json", skill_evidence),
        ("subagent-ab.json", subagent_evidence),
        ("promotion-rollback.json", lifecycle_evidence),
    ):
        _write(output / name, value)
    return 0


def _context_evidence() -> dict[str, Any]:
    policy = ContextPolicy(
        policy_id="p1-context-fixture",
        version="1",
        status="active",
        source_rules=(
            ContextSourceRule("task_spec", ("exploration",), "resident", 120, True),
            ContextSourceRule("recent_trajectory", ("exploration",), "retrieved", 100),
            ContextSourceRule("retrieved_skill", ("implementation",), "retrieved", 80),
        ),
        stage_budgets=_budgets(),
    )
    compiled = policy.compile(
        (
            {"source": "task_spec", "content": "Repair the fixture parser."},
            {
                "source": "recent_trajectory",
                "content": "The focused parser branch failed after token normalization.",
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
    with tempfile.TemporaryDirectory(prefix="praxile-p1-context-") as directory:
        root = Path(directory)
        result = AdapterRunner(EventStore(Config.load(root).paths)).execute(
            FixtureAgentAdapter(),
            AdapterTask(
                "task_context_fixture",
                "Repair the fixture parser",
                str(root),
                metadata={"trace_id": "trace_p1_context_fixture", "run_id": "run_p1_context_fixture"},
            ),
            compiled,
        )
    return {
        "schema_version": "praxile.p1_context_trace_acceptance.v1",
        "policy_id": policy.policy_id,
        "events": [
            {"event_id": event.event_id, "type": event.type, "payload": dict(event.payload)}
            for event in result.events
            if event.type == "CONTEXT_SOURCE_USAGE"
        ],
    }


def _skill_evidence() -> tuple[SkillAsset, dict[str, Any]]:
    skill = SkillAsset(
        meta=AssetMeta(
            asset_id="skill_focused_parser",
            type="skill",
            source_evidence=(EvidenceRef("event", "source_parser_failure"),),
            scope={"repository": "fixture/project"},
            applicable_conditions=("A focused parser regression is reproducible",),
            confidence=0.8,
            version="1",
            created_from_run="source_parser_run",
        ),
        name="Focused parser repair",
        input_schema={"type": "object"},
        preconditions=("The parser failure is reproducible",),
        context_requirements=("Failing test and parser source",),
        allowed_tools=("read_file", "run_test", "edit_file"),
        procedure=("Reproduce the focused failure", "Patch the narrowest parser branch"),
        verification_contract=("Focused and regression tests pass",),
        failure_modes=("The failure belongs to tokenization",),
        eval_cases=("fixture_parser_case",),
    )
    events = (
        _event("skill_inject", "CONTEXT_INJECT", {"items": [{"asset_id": skill.meta.asset_id, "asset_version": "1"}]}),
        _event("skill_reference", "SKILL_REFERENCE", {"asset_id": skill.meta.asset_id, "asset_version": "1"}),
    )
    result = SkillActivationExperiment().evaluate(
        skill,
        _arm("skill-baseline", resolved=0, tokens=1000),
        _arm("skill-candidate", resolved=1, tokens=800),
        events,
    )
    return skill, result


def _subagent_evidence() -> dict[str, Any]:
    events = (
        _event("subagent_start", "SUBAGENT_START", {"contract_id": "delegation_fixture", "child_run_id": "child_fixture"}),
        _event("subagent_end", "SUBAGENT_END", {"contract_id": "delegation_fixture", "child_run_id": "child_fixture", "status": "completed"}),
        _event(
            "subagent_merge",
            "CHECKPOINT",
            {
                "checkpoint_type": "merge_gate",
                "decision": "merge",
                "contract_id": "delegation_fixture",
                "child_run_id": "child_fixture",
                "verifier_run_id": "verifier_fixture",
            },
        ),
    )
    return SubagentComparisonExperiment().evaluate(
        _arm("no-subagent", resolved=0, tokens=1000),
        _arm("isolated-subagent", resolved=1, tokens=900),
        events,
    )


def _promotion_evidence(skill: SkillAsset) -> dict[str, Any]:
    candidate = HarnessCandidate(
        candidate_id="skill_focused_parser_v1",
        type="skill",
        component_key="skills.focused-parser",
        base_version="0",
        candidate_version="1",
        hypothesis="The focused parser skill improves the frozen fixture task.",
        source_evidence=(EvidenceRef("event", "source_parser_failure"),),
        payload=skill.to_dict(),
        risk="low",
    )
    gates = tuple(
        GateResult(
            gate=name,
            passed=True,
            evidence=(EvidenceRef("eval", f"p1_fixture_{name}"),),
            summary=f"{name} gate passed in deterministic fixture acceptance",
        )
        for name in ("evidence", "quality", "regression", "cost", "human", "rollback")
    )
    evaluation = CandidateEvaluation(
        candidate_id=candidate.candidate_id,
        baseline_ref="eval:p1_fixture_baseline",
        candidate_eval_ref="eval:p1_fixture_candidate",
        gates=gates,
        decision="promote",
        reviewer="human:p1-acceptance-maintainer",
        metrics={"resolved_delta": 1, "input_token_delta": -200, "cost_delta": 0.0},
        rollback_target={"component_key": candidate.component_key, "version": candidate.base_version},
    )
    with tempfile.TemporaryDirectory(prefix="praxile-p1-registry-") as directory:
        registry = HarnessEvolutionRegistry(Path(directory))
        registry.register(candidate)
        registry.record_evaluation(evaluation)
        registry.promote(candidate.candidate_id, approved_by="p1-acceptance-maintainer")
        promoted = _stable_registry(registry.snapshot())
        registry.rollback(candidate.component_key, approved_by="p1-acceptance-maintainer")
        rolled_back = _stable_registry(registry.snapshot())
    return {
        "schema_version": "praxile.p1_promotion_rollback_acceptance.v1",
        "candidate": candidate.to_dict(),
        "evaluation": evaluation.to_dict(),
        "after_promotion": promoted,
        "after_rollback": rolled_back,
        "assertions": {
            "promoted_version": promoted["active"][candidate.component_key]["version"],
            "rolled_back_version": rolled_back["active"][candidate.component_key]["version"],
            "candidate_status_after_rollback": rolled_back["candidates"][candidate.candidate_id]["status"],
        },
    }


def _arm(arm_id: str, *, resolved: int, tokens: int) -> ControlledArmMeasurement:
    return ControlledArmMeasurement(
        arm_id=arm_id,
        task_set_digest="sha256:p1-fixture-task-set",
        adapter="fixture",
        model="fixture-model-v1",
        evaluator="fixture-evaluator-v1",
        task_count=1,
        resolved_count=resolved,
        input_tokens=tokens,
        output_tokens=100,
        tool_calls=10,
        latency_ms=1000,
        cost=0.1,
    )


def _event(event_id: str, event_type: str, payload: dict[str, Any]) -> AgentEvent:
    return AgentEvent.create(
        event_id=event_id,
        trace_id="trace_p1_fixture",
        run_id="run_p1_fixture",
        task_id="task_p1_fixture",
        type=event_type,
        actor="fixture-agent",
        payload=payload,
    )


def _budgets() -> tuple[StageBudget, ...]:
    return tuple(
        StageBudget(stage, token_limit=1000, tool_call_limit=10, time_limit_seconds=60)
        for stage in ("exploration", "implementation", "verification")
    )


def _stable_registry(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    for record in result.get("candidates", {}).values():
        if "registered_at" in record:
            record["registered_at"] = "<generated-at>"
    for event in result.get("history", []):
        if "at" in event:
            event["at"] = "<generated-at>"
    return result


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
