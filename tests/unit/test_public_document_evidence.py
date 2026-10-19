import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2.document_evidence import project_document_read, project_deliverable_check, project_exit_code


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
