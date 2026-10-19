import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2.document_evidence import project_document_read, project_deliverable_check, project_exit_code, document_read_links


def pair(kind, action, data, status="success"):
    return (SimpleNamespace(payload={"tool": kind, "input": action}),
            SimpleNamespace(payload={"status": status, "observation": {"data": data}}))


def test_read_projects_pagination_without_content():
    call, result = pair("read_document", {"path": "PRIVATE.xlsx", "sheet": "PRIVATE", "start": 4}, {
        "path": "PRIVATE.xlsx", "source_sha256": "a" * 64, "unit_kind": "row",
        "total_units": 10, "units": ["PRIVATE", "PRIVATE"], "next_start": 6,
        "columns": 8, "columns_truncated": False, "sheet": "PRIVATE"})
    identity, facts = project_document_read(call, result)
    assert identity[0] == "read_document"
    assert facts["start_unit"] == 4 and facts["returned_units"] == 2 and facts["next_start"] == 6
    assert "PRIVATE" not in json.dumps([identity, facts])
    assert facts["semantic_quality_verified"] is False
    result.payload["observation"]["data"]["path"] = "different"
    assert project_document_read(call, result) is None


@pytest.mark.parametrize("field,value", [("source_sha256", None), ("unit_kind", "arbitrary text"),
                                        ("total_units", True), ("units", "text")])
def test_malformed_read_not_promoted_to_evidence(field, value):
    data = {"path": "a.xlsx", "source_sha256": "a" * 64, "unit_kind": "row", "total_units": 3, "units": []}
    data[field] = value
    assert project_document_read(*pair("read_document", {"path": "a.xlsx"}, data)) is None


@pytest.mark.parametrize("binding", ["model_proposed", "run_argument"])
def test_public_check_remains_distinct_from_acceptance(binding):
    call, result = pair("check_deliverables", {"contract": {"private": "PRIVATE"}}, {
        "schema_version": "praxile.deliverable_check.v1", "contract_sha256": "a" * 64,
        "contract_binding": binding, "passed": False, "official_evaluation": False,
        "semantic_quality_verified": False, "outputs": [
            {"path": "PRIVATE", "status": "failed", "reason": "PRIVATE"}]}, status="failure")
    identity, facts = project_deliverable_check(call, result)
    assert facts["contract_binding"] == binding and not facts["public_check_passed"]
    assert facts["output_status_counts"]["failed"] == 1
    assert not facts["user_acceptance_verified"] and "PRIVATE" not in json.dumps([identity, facts])
    result.payload["observation"]["data"]["official_evaluation"] = True
    assert project_deliverable_check(call, result) is None


@pytest.mark.parametrize("data,expected", [({"returncode": 0}, 0), ({"exit_code": 1}, 1),
    ({"returncode": -9, "exit_code": -9}, -9), ({"returncode": 0, "exit_code": 1}, None),
    ({"returncode": True}, None), ({"returncode": "0"}, None), ({}, None)])
def test_exit_code_normalization(data, expected):
    assert project_exit_code(data) == expected


def test_character_pagination_is_not_counted_as_identical_selection():
    call, result = pair("read_document", {"path": "private.docx", "limit": 2, "char_offset": 0}, {
        "path": "private.docx", "source_sha256": "a" * 64, "unit_kind": "block", "total_units": 2, "units": []})
    first, facts = project_document_read(call, result)
    assert facts["requested_limit"] == 2 and facts["requested_char_offset"] == 0
    call.payload["input"]["char_offset"] = 500
    second, facts = project_document_read(call, result)
    assert first != second and facts["requested_char_offset"] == 500
    assert "private" not in json.dumps(facts)


@pytest.mark.parametrize("key,value", [("char_offset", True), ("char_offset", -1),
    ("char_offset", 10000001), ("limit", 0), ("limit", 21), ("start", 100001)])
def test_invalid_read_parameters_are_not_projected(key, value):
    call, result = pair("read_document", {"path": "a.docx", key: value}, {
        "path": "a.docx", "source_sha256": "a" * 64, "unit_kind": "block", "total_units": 1, "units": []})
    assert project_document_read(call, result) is None


def read_events(index, digest="a", path="private.xlsx", task="dev", trace="trace"):
    call, result = pair("read_document", {"path": path}, {
        "path": path, "source_sha256": digest * 64, "unit_kind": "row", "total_units": 1, "units": []})
    for event, kind in ((call, "TOOL_CALL"), (result, "TOOL_RESULT")):
        event.type, event.task_id, event.trace_id = kind, task, trace
        event.tool_call_id = str(index)
        event.event_id = kind + str(index)
    return [call, result]


def test_read_links_compare_only_observed_versions_with_no_quality_claim():
    links = document_read_links([*read_events(1), *read_events(2), *read_events(3, "b")])
    assert links["TOOL_RESULT1"]["observed_version_relation"] == "unknown"
    assert links["TOOL_RESULT2"]["observed_version_relation"] == "same_bytes"
    assert links["TOOL_RESULT3"]["observed_version_relation"] == "different_bytes"
    assert links["TOOL_RESULT3"]["previous_read_event_id"] == "TOOL_RESULT2"
    assert links["TOOL_RESULT3"]["previous_observed_source_sha256"] == "a" * 64
    assert not links["TOOL_RESULT3"]["causal_effect_verified"]
    assert "private" not in json.dumps(links)


@pytest.mark.parametrize("change", [{"path": "other.xlsx"}, {"task": "another"}, {"trace": "another"}])
def test_read_links_do_not_cross_identity_boundaries(change):
    links = document_read_links([*read_events(1), *read_events(2, **change)])
    assert links["TOOL_RESULT2"]["previous_read_event_id"] is None


def test_selection_keeps_previous_read_or_omits_comparison():
    from praxile.eval.v2.development_evidence import select_evidence
    evidence = [{"event_id": "before", "task_id": "dev", "observation": "{}"},
        {"event_id": "after", "task_id": "dev", "observation": '{"previous_read_event_id":"before"}'}]
    assert select_evidence(evidence, [1, 0], 2) == [0, 1]
    assert select_evidence(evidence, [1, 0], 1) == [0]
