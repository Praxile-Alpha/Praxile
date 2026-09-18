from __future__ import annotations

import copy
import json

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter
from praxile.config import Config
from praxile.eval.v2 import CapabilityProtocol, ContextCandidate, ControlledABExperiment, EvalSchemaError, EvalTask, EvalTaskSet, HeldoutUseLedger, RepositorySpec, SWEbenchEvaluationSpec
from praxile.cli_parser import build_parser
from praxile.trace import EventStore
from praxile.eval.v2 import PublicExperimentExporter


def _protocol() -> dict:
    return {
        "schema_version": "praxile.capability_protocol.v1",
        "goal": {
            "schema_version": "praxile.capability_goal.v1",
            "goal_id": "sympy-bugfix",
            "version": "1",
            "task_family": "symbolic-math-bugfix",
            "objective": "Resolve held-out regressions without sacrificing safety.",
            "success_criteria": ["heldout_resolution"],
        },
        "operationalization": {
            "schema_version": "praxile.operationalization_hypothesis.v1",
            "hypothesis_id": "focused-investigation",
            "version": "1",
            "goal_id": "sympy-bugfix",
            "rationale": "Less irrelevant exploration may save tokens.",
            "proxy_metrics": ["token_count", "tool_calls"],
            "known_failure_modes": ["proxy improves while resolution does not"],
        },
        "evaluation": {
            "schema_version": "praxile.evaluation_contract.v1",
            "contract_id": "sympy-lite-2",
            "version": "1",
            "goal_id": "sympy-bugfix",
            "dataset_name": "fixture",
            "split": "test",
            "development_task_ids": ["dev-task"],
            "heldout_task_ids": ["heldout-task"],
            "evaluator_owner": "control_plane",
            "evaluator_name": "fixture-evaluator",
            "primary_metric": "resolved",
        },
        "information_boundary": {
            "schema_version": "praxile.information_boundary.v1",
            "boundary_id": "no-evaluator-leak",
            "version": "1",
            "isolation_level": "logical_only",
            "forbidden_metadata_keys": ["solution"],
        },
        "terminal_selection_rule": {
            "schema_version": "praxile.terminal_selection_rule.v1",
            "rule_id": "heldout-first",
            "version": "1",
            "minimum_heldout_gains": 1,
            "maximum_heldout_regressions": 0,
            "require_human_approval": True,
        },
    }


def _tasks(*, metadata: dict | None = None) -> EvalTaskSet:
    tasks = tuple(
        EvalTask(
            task_id=task_id,
            instruction="Fix the regression",
            repository=RepositorySpec("owner/repo", "abc123", "https://example.invalid/repo.git"),
            evaluation=SWEbenchEvaluationSpec("fixture", "test", reference_patch="private patch content 123"),
            metadata=metadata or {},
        )
        for task_id in ("dev-task", "heldout-task")
    )
    return EvalTaskSet("fixture-set", "fixture", "test", tasks)


def test_protocol_roundtrip_digest_and_cli_check(tmp_path, capsys) -> None:
    value = _protocol()
    path = tmp_path / "capability.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    protocol = CapabilityProtocol.load(path)
    assert CapabilityProtocol.from_dict(protocol.to_dict()).digest == protocol.digest
    protocol.validate_run(_tasks(), candidate_payload={"context_item": {"content": "Focus on regression"}}, evaluator_identity={"name": "fixture-evaluator"})
    adapter_task = _tasks().tasks[0].to_adapter_task(str(tmp_path), trace_id="trace-1", run_id="run-1")
    assert "evaluation" not in adapter_task.metadata
    assert "private patch content 123" not in json.dumps(adapter_task.to_dict())
    args = build_parser().parse_args(["eval", "capability-check", str(path), "--json"])
    assert args.func(args, tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["digest"] == protocol.digest


def test_protocol_rejects_answer_bearing_metadata_and_candidate() -> None:
    protocol = CapabilityProtocol.from_dict(_protocol())
    with pytest.raises(EvalSchemaError, match="metadata leaks"):
        protocol.validate_run(_tasks(metadata={"nested": {"solution": "secret"}}), candidate_payload={"context_item": {"content": "Focus"}}, evaluator_identity={"name": "fixture-evaluator"})
    with pytest.raises(EvalSchemaError, match="candidate includes private"):
        protocol.validate_run(_tasks(), candidate_payload={"context_item": {"content": "private patch content 123"}}, evaluator_identity={"name": "fixture-evaluator"})
    with pytest.raises(EvalSchemaError, match="candidate context contains"):
        protocol.validate_run(_tasks(), candidate_payload={"context_item": {"rubric": "private", "content": "Focus"}}, evaluator_identity={"name": "fixture-evaluator"})
    with pytest.raises(EvalSchemaError, match="candidate includes private"):
        protocol.validate_run(
            _tasks(),
            candidate_payload={"context_item": {"content": "Focus"},
                               "representation_options": {"raw_episode": "private patch content 123"}},
            evaluator_identity={"name": "fixture-evaluator"},
        )


def test_protocol_rejects_partition_overlap_and_unowned_evaluator() -> None:
    value = _protocol()
    value["evaluation"]["development_task_ids"] = ["heldout-task"]
    with pytest.raises(EvalSchemaError, match="overlap"):
        CapabilityProtocol.from_dict(value)
    value = _protocol()
    value["evaluation"]["evaluator_owner"] = "proposal_composer"
    with pytest.raises(EvalSchemaError, match="independent"):
        CapabilityProtocol.from_dict(value)
    value = _protocol()
    value["evaluation"]["heldout_task_ids"] = ["unknown-task"]
    with pytest.raises(EvalSchemaError, match="partition mismatch"):
        CapabilityProtocol.from_dict(value).validate_run(_tasks(), candidate_payload={"context_item": {"content": "Focus"}}, evaluator_identity={"name": "fixture-evaluator"})
    with pytest.raises(EvalSchemaError, match="evaluator name"):
        CapabilityProtocol.from_dict(_protocol()).validate_run(_tasks(), candidate_payload={"context_item": {"content": "Focus"}}, evaluator_identity={"name": "other"})


def test_terminal_selection_refuses_proxy_only_and_unknown_outcomes() -> None:
    protocol = CapabilityProtocol.from_dict(_protocol())
    rows = [
        {"task_id": "dev-task", "transition": "unchanged", "token_delta": -100, "tool_call_delta": -2},
        {"task_id": "heldout-task", "transition": "unchanged", "token_delta": -100, "tool_call_delta": -2},
    ]
    decision = protocol.select_terminal(rows)
    assert decision["decision"] == "inconclusive"
    assert decision["development_proxy_deltas"] == {"token_count": -100.0, "tool_calls": -2.0}
    improved = copy.deepcopy(rows)
    improved[1]["transition"] = "improved"
    assert protocol.select_terminal(improved)["decision"] == "human_review_required"
    improved[1]["transition"] = "unknown"
    assert protocol.select_terminal(improved)["decision"] == "inconclusive"


def test_protocol_rejects_freeform_secrets_and_auto_promotion() -> None:
    value = _protocol()
    value["evaluation"]["expected_answers"] = ["leak"]
    with pytest.raises(EvalSchemaError, match="unknown fields"):
        CapabilityProtocol.from_dict(value)
    value = _protocol()
    value["terminal_selection_rule"]["require_human_approval"] = False
    with pytest.raises(EvalSchemaError, match="human approval"):
        CapabilityProtocol.from_dict(value)
    value = _protocol()
    value["information_boundary"]["isolation_level"] = "os_sandbox"
    with pytest.raises(EvalSchemaError, match="not implemented"):
        CapabilityProtocol.from_dict(value)


def test_ab_rejects_leak_before_creating_experiment(tmp_path) -> None:
    protocol = CapabilityProtocol.from_dict(_protocol())
    candidate = ContextCandidate(
        candidate_id="leaking-context", version="1", title="Leaking context",
        candidate_type="experience_activation",
        context_item={"content": "private patch content 123"},
        confidence=0.7, evidence_refs=("event:source",),
        expected_effect={"tool_calls": "decrease"},
    )
    state = tmp_path / "state"
    store = EventStore(Config.load(tmp_path).paths)
    experiment = ControlledABExperiment(state, store)
    class Evaluator:
        def identity(self):
            return {"name": "fixture-evaluator"}

    with pytest.raises(EvalSchemaError, match="candidate includes private"):
        experiment.run(
            _tasks(), adapter=FixtureAgentAdapter(), evaluator=Evaluator(),
            baseline_policy=AdapterPolicy(policy_id="baseline"), candidate=candidate,
            model={"model_name_or_path": "fixture"}, experiment_id="must-not-exist",
            capability_protocol=protocol,
        )
    assert not (state / "eval" / "v2" / "experiments" / "must-not-exist").exists()


def test_heldout_claim_blocks_new_experiment_even_if_contract_id_changes(tmp_path) -> None:
    protocol = CapabilityProtocol.from_dict(_protocol())
    ledger = HeldoutUseLedger(tmp_path)
    first = ledger.reserve(protocol, experiment_id="first", candidate_digest="candidate-a", task_set_digest="tasks-a")
    assert first["status"] == "reserved"
    assert ledger.reserve(protocol, experiment_id="first", candidate_digest="candidate-a", task_set_digest="tasks-a") == first
    with pytest.raises(EvalSchemaError, match="already reserved"):
        ledger.reserve(protocol, experiment_id="second", candidate_digest="candidate-a", task_set_digest="tasks-a")
    changed = _protocol()
    changed["evaluation"]["contract_id"] = "renamed-contract"
    with pytest.raises(EvalSchemaError, match="already reserved"):
        ledger.reserve(CapabilityProtocol.from_dict(changed), experiment_id="second", candidate_digest="candidate-a", task_set_digest="tasks-a")
    overlapping = _protocol()
    overlapping["evaluation"]["contract_id"] = "expanded-contract"
    overlapping["evaluation"]["heldout_task_ids"] = ["heldout-task", "another-heldout-task"]
    with pytest.raises(EvalSchemaError, match="task ID was already reserved"):
        ledger.reserve(CapabilityProtocol.from_dict(overlapping), experiment_id="second", candidate_digest="candidate-a", task_set_digest="tasks-b")
    ledger.mark_evaluated(protocol, experiment_id="first", report_digest="sha256:report")
    assert ledger.status(protocol) == "evaluated"


def test_cli_and_public_export_hide_intermediate_heldout_feedback(tmp_path) -> None:
    config = Config.load(tmp_path)
    protocol = CapabilityProtocol.from_dict(_protocol())
    ledger = HeldoutUseLedger(config.paths.state)
    ledger.reserve(protocol, experiment_id="sealed-run", candidate_digest="candidate", task_set_digest="tasks")
    parser = build_parser()
    diagnose = parser.parse_args(["eval", "diagnose", "sealed-run.baseline"])
    with pytest.raises(PermissionError, match="sealed"):
        diagnose.func(diagnose, tmp_path)
    analyze = parser.parse_args(["eval", "ab-analyze", "sealed-run"])
    with pytest.raises(PermissionError, match="sealed"):
        analyze.func(analyze, tmp_path)
    with pytest.raises(EvalSchemaError, match="sealed"):
        ControlledABExperiment(config.paths.state, EventStore(config.paths)).analyze("sealed-run")
    exporter = PublicExperimentExporter(config.paths.state, EventStore(config.paths))
    with pytest.raises(EvalSchemaError, match="cannot be exported"):
        exporter.export("sealed-run", tmp_path / "public")
