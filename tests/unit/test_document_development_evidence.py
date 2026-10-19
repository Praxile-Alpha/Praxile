from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from praxile.eval.v2.document_evidence import project_layout


def pair():
    call = SimpleNamespace(payload={"action": {
        "type": "check_document_layout", "path": "private.docx", "max_pages": 1}})
    result = SimpleNamespace(payload={"status": "failure", "observation": {"data": {
        "schema_version": "praxile.document_layout.v1", "path": "private.docx",
        "pages": 2, "max_pages": 1, "rendered": True, "page_limit_met": False,
        "constraint_source": "action_argument", "text": "SECRET RUBRIC",
        "renderer_executable": "/private/renderer", "visual_quality_verified": True}}})
    return call, result


def test_layout_projection_redacts_text_and_does_not_claim_quality():
    call, result = pair()
    identity, facts = project_layout(call, result)
    assert identity[0] == "check_document_layout"
    assert facts["pages"] == 2 and facts["page_limit_met"] is False
    assert facts["visual_quality_verified"] is False
    assert "private" not in str((identity, facts)) and "SECRET" not in str((identity, facts))
    other = deepcopy(call)
    other.payload["action"]["path"] = "different.docx"
    assert project_layout(other, result) is None


@pytest.mark.parametrize("field,value", [
    ("pages", True), ("pages", 0), ("pages", 2001), ("pages", "2"),
    ("max_pages", True), ("max_pages", 2), ("page_limit_met", True),
    ("rendered", False), ("constraint_source", "official_rubric"),
    ("schema_version", "unknown"),
])
def test_invalid_layout_fact_is_not_evidence(field, value):
    call, result = pair()
    result.payload["observation"]["data"][field] = value
    assert project_layout(call, result) is None


def test_no_limit_is_unknown_not_passed_constraint():
    call, result = pair()
    call.payload["action"].pop("max_pages")
    result.payload["status"] = "success"
    result.payload["observation"]["data"].update(max_pages=None, page_limit_met=None)
    assert project_layout(call, result)[1]["page_limit_met"] is None


def test_input_payload_supported_and_status_mismatch_rejected():
    call, result = pair()
    call.payload = {"tool": "check_document_layout", "input": call.payload["action"]}
    assert project_layout(call, result) is not None
    result.payload["status"] = "success"
    assert project_layout(call, result) is None


def test_collector_projects_layout_under_development_membership(tmp_path, monkeypatch):
    from praxile.eval.v2 import development_evidence as module

    manifest = SimpleNamespace(reproducibility={"task_set": {
        "tasks": [{"task_id": "dev-task"}], "digest": "tasks-digest"}},
        reproducibility_digest="manifest-digest")
    plan = {"schema_version": "praxile.ab_experiment.v1", "track": "development",
        "experiment_id": "experiment", "baseline_run_id": "run", "task_set_digest": "tasks-digest"}
    report = {"eval_run_id": "run", "manifest_digest": "manifest-digest", "tasks": [{
        "task_id": "dev-task", "trace_id": "trace", "agent_run_id": "agent",
        "eval_run_id": "run", "manifest_digest": "manifest-digest"}]}
    monkeypatch.setattr(module, "ImmutableManifestStore", lambda root: SimpleNamespace(
        load=lambda run: manifest, path_for=lambda run: tmp_path / "run/manifest.json"))
    monkeypatch.setattr(module, "read_json", lambda path, default: report if path.name == "report.json" else plan)
    call, result = pair()
    for event, kind in [(call, "TOOL_CALL"), (result, "TOOL_RESULT")]:
        event.type, event.event_id = kind, kind.lower()
        event.trace_id, event.task_id, event.run_id = "trace", "dev-task", "agent"
        event.tool_call_id = "call"
        event.to_json = lambda event=event: json.dumps(event.payload, sort_keys=True)
    store = SimpleNamespace(list_events=lambda **kwargs: [call, result])
    args = dict(experiment_id="experiment", arm="baseline",
                development_task_ids=["dev-task"], heldout_task_ids=["heldout"])
    bundle = module.collect_development_evidence(tmp_path, store, **args)
    assert bundle["eligible_count"] == 1
    facts = json.loads(bundle["evidence"][0]["observation"])
    assert facts["pages"] == 2 and facts["call_event_id"] == "tool_call"
    assert "SECRET" not in json.dumps(bundle) and "private.docx" not in json.dumps(bundle)
    plan["track"] = "clean"
    with pytest.raises(module.EvalSchemaError, match="explicit development"):
        module.collect_development_evidence(tmp_path, store, **args)
