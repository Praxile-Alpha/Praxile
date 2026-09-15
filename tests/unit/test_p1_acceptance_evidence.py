from __future__ import annotations

import json
from pathlib import Path


ROOT = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "control_plane"
    / "P1_FIXTURE_ACCEPTANCE_V1"
)


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_p1_acceptance_manifest_is_explicit_about_fixture_scope() -> None:
    manifest = _load("manifest.json")

    assert manifest["execution_mode"] == "deterministic_fixture"
    assert len(manifest["artifacts"]) == 4
    assert any("does not claim real-model" in item for item in manifest["non_claims"])


def test_p1_context_skill_subagent_and_lifecycle_evidence_is_complete() -> None:
    context = _load("context-source-trace.json")
    skill = _load("skill-activation-ab.json")
    subagent = _load("subagent-ab.json")
    lifecycle = _load("promotion-rollback.json")

    sources = {event["payload"]["source"]: event["payload"] for event in context["events"]}
    assert sources["recent_trajectory"]["compression"][0]["decision"] == "compressed"
    assert sources["retrieved_skill"]["status"] == "not_selected"
    assert skill["invariant_check"]["valid"] is True
    assert skill["activation"]["injected"] is True
    assert skill["activation"]["referenced"] is True
    assert skill["attribution"]["status"] == "credited"
    assert subagent["invariant_check"]["valid"] is True
    assert subagent["delegation"]["trace_complete"] is True
    assert subagent["delegation"]["completed_delegations"] == [
        {
            "child_run_id": "child_fixture",
            "contract_id": "delegation_fixture",
            "end_event_id": "subagent_end",
            "merge_gate_event_id": "subagent_merge",
            "start_event_id": "subagent_start",
            "verifier_run_id": "verifier_fixture",
        }
    ]
    assert subagent["attribution"]["status"] == "credited"
    assert lifecycle["assertions"] == {
        "candidate_status_after_rollback": "rolled_back",
        "promoted_version": "1",
        "rolled_back_version": "0",
    }
