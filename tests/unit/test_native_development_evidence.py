import hashlib
import json

import pytest

from praxile.eval.v2.native_development_evidence import collect_native_evidence, design_from_native
from praxile.eval.v2.schema import EvalSchemaError


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "result.json"
    value = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
        "trajectory": {"task_id": "dev-task", "start_time": "2026-10-19T00:00:00Z",
            "user_task": "DO_NOT_EXPORT", "result": {"summary": "DO_NOT_EXPORT"},
            "actions": [{"step": 1, "action_type": "run_command", "input": {"command": "DO_NOT_EXPORT"},
                "status": "failure", "observation": {"data": {"exit_code": 1, "stdout": "DO_NOT_EXPORT"}}}]},
        "official_details": "DO_NOT_EXPORT"}
    path.write_text(json.dumps(value))
    return path


def options(path):
    return dict(expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), track="development",
                development_task_ids=["dev-task"], heldout_task_ids=["heldout"])


def test_projection_excludes_raw_text_and_grader_feedback(source):
    result = collect_native_evidence(source, **options(source))
    assert "DO_NOT_EXPORT" not in json.dumps(result)
    facts = json.loads(result["evidence"][0]["observation"])
    assert facts["exit_code"] == 1 and facts["state_equivalence"] == "unknown"
    assert result["eligible_count"] == 1
    assert result["promotion_eligible"] is result["provenance_attested"] is False
    assert result == collect_native_evidence(source, **options(source))


@pytest.mark.parametrize("field,value", [("track", "heldout"), ("heldout_task_ids", ["dev-task"]),
    ("development_task_ids", ["another"]), ("expected_sha256", "0" * 64), ("limit", 21),
    ("development_task_ids", "dev-task"), ("heldout_task_ids", [None]), ("expected_sha256", None)])
def test_invalid_source_cannot_reach_model(source, field, value):
    args = {**options(source), field: value}
    with pytest.raises(EvalSchemaError):
        design_from_native(source, **args, router=None)


def test_empty_evidence_abstains_without_model(source):
    value = json.loads(source.read_text())
    value["trajectory"]["actions"] = []
    source.write_text(json.dumps(value))
    result = design_from_native(source, **options(source), router=None)
    assert result["decision"] == "NO_ENTRY" and result["candidates"] == []


def test_valid_projection_is_passed_to_existing_designer(source, monkeypatch):
    import praxile.eval.v2.candidate_designer as designer
    def fake(**kwargs):
        assert len(kwargs["evidence"]) == 1
        assert kwargs["development_task_ids"] == ["dev-task"]
        assert "DO_NOT_EXPORT" not in json.dumps(kwargs)
        return {"decision": "NO_ENTRY", "promotion_eligible": False}
    monkeypatch.setattr(designer, "design_development_candidates", fake)
    assert design_from_native(source, **options(source))["decision"] == "NO_ENTRY"


def test_cli_writes_private_bundle_once(source, monkeypatch, capsys):
    import stat
    from praxile.eval.v2.native_development_evidence import main
    output = source.parent / "projected.json"
    monkeypatch.setattr("sys.argv", ["project-native", "--result", str(source), "--sha256",
        options(source)["expected_sha256"], "--development-task-id", "dev-task", "--track", "development",
        "--output", str(output)])
    main()
    before = output.read_bytes()
    assert "no model request" in capsys.readouterr().out
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        main()
    assert output.read_bytes() == before
