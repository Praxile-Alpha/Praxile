from __future__ import annotations

import json
from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy
from praxile.control_plane import (
    EvidenceRef,
    ExecutableHarnessManifest,
    ExecutorCompatibilityMatrix,
    HarnessCandidate,
    HarnessEvolutionRegistry,
    HarnessMechanism,
    PromotionGateEvaluator,
)
from praxile.eval.v2 import EvalSchemaError


def _candidate(executor: str = "mini-swe-agent.minimax-m3", family: str = "sympy-bugfix") -> HarnessCandidate:
    return HarnessCandidate(
        candidate_id="candidate-lab",
        type="context_policy",
        component_key="context.focused",
        base_version="1",
        candidate_version="2",
        hypothesis="Focused context improves symbolic bug fixes.",
        source_evidence=(EvidenceRef("eval", "eval-source"),),
        payload={"context": "focused"},
        executor_profile=executor,
        task_family=family,
    )


def _manifest(**overrides) -> dict:
    value = {
        "schema_version": "praxile.executable_harness_manifest.v1",
        "lab_id": "lab-1",
        "version": "1",
        "experiment_mode": "creation",
        "isolation_mode": "worktree",
        "candidate": _candidate().to_dict(),
        "adapter_name": "mini-swe-agent",
        "executor_config": {"cost_tracking": "ignore_errors", "timeout_seconds": 1800},
        "model": {"model_name_or_path": "openai/MiniMax-M3"},
        "evaluator": {"name": "swebench", "version": "1"},
        "baseline_policy": AdapterPolicy(policy_id="baseline", version="1").to_dict(),
        "candidate_policy": AdapterPolicy(policy_id="candidate", version="2", context=({"content": "focus"},)).to_dict(),
        "development_task_ids": ["dev-1"],
        "heldout_task_ids": ["heldout-1"],
        "development_task_digest": "sha256:" + "1" * 64,
        "heldout_task_digest": "sha256:" + "2" * 64,
        "repetitions": 2,
        "mechanisms": [{"mechanism_id": "context-router", "coverage_class": "C"}],
        "base_harness_ref": None,
    }
    value.update(overrides)
    return value


def test_manifest_freezes_composite_promotion_key_and_dev_heldout_split() -> None:
    manifest = ExecutableHarnessManifest.from_dict(_manifest())
    assert manifest.candidate.promotion_key == "context.focused::mini-swe-agent.minimax-m3::sympy-bugfix"
    assert manifest.digest.startswith("sha256:")
    with pytest.raises(EvalSchemaError, match="overlap"):
        ExecutableHarnessManifest.from_dict(_manifest(heldout_task_ids=["dev-1"]))
    with pytest.raises(EvalSchemaError, match="at least two"):
        ExecutableHarnessManifest.from_dict(_manifest(repetitions=1))


def test_legacy_registry_migrates_active_pointer_to_composite_key(tmp_path: Path) -> None:
    path = tmp_path / ".praxile" / "control-plane" / "registry.json"
    path.parent.mkdir(parents=True)
    old_candidate = _candidate("default", "default").to_dict()
    old_candidate["schema_version"] = "praxile.harness_candidate.v1"
    old_candidate.pop("executor_profile")
    old_candidate.pop("task_family")
    old_candidate.pop("promotion_key")
    path.write_text(json.dumps({
        "schema_version": "praxile.harness_registry.v1",
        "candidates": {"candidate-lab": {"candidate": old_candidate, "status": "active"}},
        "evaluations": {},
        "active": {"context.focused": {"candidate_id": "candidate-lab", "version": "2"}},
        "history": [],
    }), encoding="utf-8")
    state = HarnessEvolutionRegistry(tmp_path).snapshot()
    assert state["schema_version"] == "praxile.harness_registry.v2"
    assert state["active"]["context.focused::default::default"]["version"] == "2"


def test_executor_matrix_keeps_executor_specific_direction() -> None:
    reports = [
        {"executor_profile": "mini-swe-agent.model-a", "task_family": "sympy", "promotion_key": "a", "promotion_eligible": True, "dead_mechanisms": [], "splits": {"heldout": {"delta": {"estimate": 0.2, "ci95": [0.0, 0.4]}}}},
        {"executor_profile": "mini-swe-agent.model-b", "task_family": "sympy", "promotion_key": "b", "promotion_eligible": False, "dead_mechanisms": ["router"], "splits": {"heldout": {"delta": {"estimate": -0.1, "ci95": [-0.3, 0.1]}}}},
    ]
    matrix = ExecutorCompatibilityMatrix.build(reports)
    assert matrix["executor_sensitive"] is True
    assert matrix["promotion_scope"] == "component_id + executor_profile + task_family"


def test_lab_report_drives_all_six_promotion_gates() -> None:
    candidate = _candidate()
    report = {
        "schema_version": "praxile.executable_harness_report.v1",
        "lab_id": "lab-positive",
        "promotion_key": candidate.promotion_key,
        "promotion_eligible": True,
        "repetitions": 3,
        "dead_mechanisms": [],
        "invariant_check": {"valid": True},
        "splits": {
            "heldout": {
                "delta": {"estimate": 0.2, "ci95": [0.01, 0.4]},
                "regressions": 0,
                "efficiency": {"cost_delta_mean": -0.1},
            }
        },
    }
    evaluation = PromotionGateEvaluator().evaluate_lab(
        candidate,
        report,
        reviewer="maintainer",
        human_approved=True,
    )
    assert evaluation.decision == "promote"
    assert all(gate.passed for gate in evaluation.gates)
    assert evaluation.rollback_target["promotion_key"] == candidate.promotion_key
