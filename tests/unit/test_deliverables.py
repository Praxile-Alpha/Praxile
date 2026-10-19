import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.deliverables import verify_deliverables
from praxile.eval.v2.schema import EvalSchemaError
from praxile.tools import ToolRegistry
from praxile.action_schema import ActionSchemaRegistry


def contract(path="report.json", media="application/json", **kwargs):
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
            "outputs": [{"path": path, "media_type": media, **kwargs}]}


def test_json_structure_and_runtime_evidence(tmp_path):
    (tmp_path / "report.json").write_text('{"summary":"done"}')
    action = {"type": "check_deliverables", "contract": contract(required_fields=["summary"])}
    assert ActionSchemaRegistry().validate(action)[0]
    tools = ToolRegistry(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    result = tools.execute(action, task_id="test", step=1)
    assert result["status"] == "success"
    report = result["data"]
    assert not report["official_evaluation"]
    assert not report["semantic_quality_verified"]
    assert len(report["outputs"][0]["sha256"]) == 64
    assert not verify_deliverables(tmp_path, contract(required_fields=["missing"]))["passed"]


@pytest.mark.parametrize("content", ['{"value":NaN}', '{"value":', 'not json'])
def test_invalid_json_is_not_passed(tmp_path, content):
    (tmp_path / "report.json").write_text(content)
    report = verify_deliverables(tmp_path, contract())
    assert not report["passed"]


def test_csv_checks_every_row(tmp_path):
    path = tmp_path / "table.csv"
    request = contract("table.csv", "text/csv", required_fields=["name", "count"])
    path.write_text("name,count\none,2\n")
    assert verify_deliverables(tmp_path, request)["passed"]
    path.write_text("name,count\none,2,extra\n")
    assert not verify_deliverables(tmp_path, request)["passed"]
    path.write_text("name,name\none,two\n")
    assert not verify_deliverables(tmp_path, request)["passed"]


def test_unsupported_missing_optional_and_size_limit(tmp_path):
    assert not verify_deliverables(tmp_path, contract())["passed"]
    assert verify_deliverables(tmp_path, contract(required=False))["passed"]
    (tmp_path / "report.json").write_bytes(b"{}")
    assert not verify_deliverables(tmp_path, contract(), max_bytes=1)["passed"]
    report = verify_deliverables(tmp_path, contract(media="application/x-unknown"))
    assert report["outputs"][0]["status"] == "unsupported"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "a/../secret", "a\\secret"])
def test_paths_are_normalized_and_bounded(tmp_path, path):
    with pytest.raises(EvalSchemaError):
        verify_deliverables(tmp_path, contract(path))


def test_symlinks_and_sensitive_paths_are_blocked(tmp_path):
    (tmp_path / "source").write_text("{}")
    (tmp_path / "report.json").symlink_to(tmp_path / "source")
    assert verify_deliverables(tmp_path, contract())["outputs"][0]["reason"] == "symlink_forbidden"
    (tmp_path / ".env").write_text("SECRET=hidden")
    tools = ToolRegistry(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    result = tools.execute({"type": "check_deliverables", "contract": contract(".env", "text/plain")}, task_id="t", step=1)
    assert result["status"] == "blocked"


def test_contract_scope_and_duplicates(tmp_path):
    value = contract()
    value["source"] = "hidden_rubric"
    with pytest.raises(EvalSchemaError):
        verify_deliverables(tmp_path, value)
    value = contract()
    value["outputs"] *= 2
    with pytest.raises(EvalSchemaError):
        verify_deliverables(tmp_path, value)


def test_csv_minimum_rows(tmp_path):
    (tmp_path / "table.csv").write_text("name\nfirst\n")
    result = verify_deliverables(tmp_path, contract("table.csv", "text/csv", structure={"min_rows": 2}))
    assert not result["passed"]
    assert result["outputs"][0]["structure_checks"] == [
        {"requirement": "min_rows", "expected": 2, "actual": 1, "passed": False}]


@pytest.mark.parametrize("structure", [{"min_rows": True}, {"min_rows": -1},
    {"min_rows": "2"}, {"min_pages": 2}, {"unknown": 1}, []])
def test_invalid_or_wrong_format_constraints_are_not_ignored(tmp_path, structure):
    with pytest.raises(EvalSchemaError):
        verify_deliverables(tmp_path, contract("table.csv", "text/csv", structure=structure))


def state_contract(**states):
    return {"schema_version": "praxile.deliverable_contract.v2", "source": "user_request",
            "outputs": [], "state_equals": states}


@pytest.mark.parametrize("observed,status", [
    (None, "unknown"), ({}, "unknown"), ({"browser": {"session_active": False}}, "mismatched"),
    ({"browser": {"session_active": 1}}, "mismatched"),
    ({"browser": {"session_active": True}}, "matched")])
def test_state_contract_requires_observed_exact_state(tmp_path, observed, status):
    request = state_contract(**{"browser.session_active": True})
    result = verify_deliverables(tmp_path, request, observed_state=observed)
    assert result["passed"] is (status == "matched")
    assert result["state_check"]["checks"][0]["status"] == status
    assert not result["state_transitions_verified"]
    assert not result["contract_provenance_verified"]


@pytest.mark.parametrize("states", [{}, [], {"bad..path": True}, {"browser.origin": None},
                                      {"browser.origin": "x" * 2001}, {"count": 1.5}])
def test_state_contract_invalid_constraints_rejected(tmp_path, states):
    request = state_contract()
    request["state_equals"] = states
    with pytest.raises(EvalSchemaError):
        verify_deliverables(tmp_path, request)


def test_state_success_cannot_hide_failed_file_requirement(tmp_path):
    request = state_contract(**{"browser.session_active": False})
    request["outputs"] = contract()["outputs"]
    result = verify_deliverables(tmp_path, request, observed_state={"browser": {"session_active": False}})
    assert result["state_check"]["status"] == "matched"
    assert not result["passed"]


def test_tool_uses_live_observer_not_model_claims(tmp_path):
    class Browser:
        def observe_state(self):
            return {"session_active": False}
    tools = ToolRegistry(Config(default_config(tmp_path), ProjectPaths(tmp_path)), browser=Browser())
    result = tools.execute({"type": "check_deliverables",
        "contract": state_contract(**{"browser.session_active": True}),
        "observed_state": {"browser": {"session_active": True}}}, task_id="t", step=1)
    assert result["status"] == "failure"
    assert result["data"]["state_check"]["checks"][0]["status"] == "mismatched"


def test_tool_observation_failure_is_unknown(tmp_path):
    class Browser:
        def observe_state(self):
            raise RuntimeError("unavailable")
    tools = ToolRegistry(Config(default_config(tmp_path), ProjectPaths(tmp_path)), browser=Browser())
    result = tools.execute({"type": "check_deliverables",
        "contract": state_contract(**{"browser.session_active": False})}, task_id="t", step=1)
    assert result["status"] == "failure"
    assert result["data"]["state_check"]["checks"][0]["status"] == "unknown"
