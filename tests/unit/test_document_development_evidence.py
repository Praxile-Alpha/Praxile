from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from praxile.eval.v2.document_evidence import project_layout, project_revision, revision_layout_links


def revision_events():
    call = SimpleNamespace(type="TOOL_CALL", tool_call_id="revision", event_id="revision-call",
        payload={"action": {"type": "revise_docx", "path": "private.docx",
            "expected_sha256": "a" * 64, "document": {"blocks": ["PRIVATE BODY"]}}})
    result = SimpleNamespace(type="TOOL_RESULT", tool_call_id="revision", event_id="revision-result",
        payload={"status": "success", "observation": {"data": {"path": "private.docx",
            "before_sha256": "a" * 64, "sha256": "b" * 64, "changed": True}}})
    return call, result


def test_revision_projects_digest_transition_not_document_body():
    call, result = revision_events()
    projected = project_revision(call, result)
    assert projected[0][0] == "revise_docx"
    assert projected[1]["before_sha256"] == "a" * 64
    assert projected[1]["after_sha256"] == "b" * 64
    assert "PRIVATE" not in str(projected) and "private.docx" not in str(projected)
    result.payload["observation"]["data"]["before_sha256"] = "c" * 64
    assert project_revision(call, result) is None


def test_layout_link_requires_unique_prior_matching_version():
    revision_call, revision_result = revision_events()
    call, result = pair()
    for event, kind in [(call, "TOOL_CALL"), (result, "TOOL_RESULT")]:
        event.type, event.tool_call_id, event.event_id = kind, "layout", kind
    data = result.payload["observation"]["data"]
    data["source_sha256"] = "b" * 64
    events = [revision_call, revision_result, call, result]
    link = revision_layout_links(events)[result.event_id]
    assert link["revision_event_id"] == revision_result.event_id
    assert link["causal_effect_verified"] is False
    assert revision_layout_links([call, result, revision_call, revision_result])[result.event_id]["revision_event_id"] is None
    data["source_sha256"] = "c" * 64
    assert revision_layout_links(events)[result.event_id]["revision_event_id"] is None
    data["source_sha256"] = "b" * 64
    duplicate = deepcopy(revision_result)
    duplicate.event_id = "other-revision"
    assert revision_layout_links([revision_call, revision_result, duplicate, call, result])[result.event_id]["revision_event_id"] is None


def layout_events(prefix, digest, pages, limit=1, renderer="/usr/bin/soffice"):
    call, result = pair()
    call.payload["action"]["max_pages"] = limit
    data = result.payload["observation"]["data"]
    data.update(source_sha256=digest, pages=pages, max_pages=limit,
                page_limit_met=pages <= limit, renderer_executable=renderer)
    result.payload["status"] = "success" if pages <= limit else "failure"
    for event, kind in [(call, "TOOL_CALL"), (result, "TOOL_RESULT")]:
        event.type, event.tool_call_id, event.event_id = kind, prefix, prefix + kind
    return call, result


def test_before_after_page_comparison_uses_versions_not_nearest_step():
    before = layout_events("before", "a" * 64, 3)
    revision = revision_events()
    after = layout_events("after", "b" * 64, 2)
    unrelated = layout_events("unrelated", "c" * 64, 20)
    events = [*before, *unrelated, *revision, *after]
    link = revision_layout_links(events)[after[1].event_id]
    assert link["page_delta"] == -1
    assert link["before_layout_event_id"] == before[1].event_id
    assert link["causal_effect_verified"] is False
    assert "soffice" not in str(link)
    assert revision_layout_links([*revision, *before, *after])[after[1].event_id]["page_delta"] is None


@pytest.mark.parametrize("kind", ["renderer", "limit", "duplicate", "missing"])
def test_page_comparison_abstains_for_noncomparable_evidence(kind):
    before = layout_events("before", "a" * 64, 3)
    revision = revision_events()
    after = layout_events("after", "b" * 64, 2)
    if kind == "renderer":
        after[1].payload["observation"]["data"]["renderer_executable"] = "/different/soffice"
    if kind == "limit":
        before = layout_events("before", "a" * 64, 3, limit=2)
    earlier = list(before)
    if kind == "duplicate":
        earlier.extend(layout_events("repeat", "a" * 64, 3))
    if kind == "missing":
        earlier = []
    link = revision_layout_links([*earlier, *revision, *after])[after[1].event_id]
    assert link["page_delta"] is None and link["page_comparison_status"] == "unknown"


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
    revision_call, revision_result = revision_events()
    for event in [revision_call, revision_result]:
        event.trace_id, event.task_id, event.run_id = "trace", "dev-task", "agent"
        event.to_json = lambda event=event: json.dumps(event.payload, sort_keys=True)
    result.payload["observation"]["data"]["source_sha256"] = "b" * 64
    store.list_events = lambda **kwargs: [revision_call, revision_result, call, result]
    linked = module.collect_development_evidence(tmp_path, store, **args)
    assert linked["eligible_count"] == 2
    assert [item["event_id"] for item in linked["evidence"]] == ["revision-result", "tool_result"]
    facts = json.loads(linked["evidence"][1]["observation"])
    assert facts["revision_event_id"] == "revision-result"
    assert "PRIVATE BODY" not in json.dumps(linked)
    bounded = module.collect_development_evidence(tmp_path, store, limit=1, **args)
    assert [item["event_id"] for item in bounded["evidence"]] == ["revision-result"]
    assert bounded["omitted_count"] == 1
    plan["track"] = "clean"
    with pytest.raises(module.EvalSchemaError, match="explicit development"):
        module.collect_development_evidence(tmp_path, store, **args)
