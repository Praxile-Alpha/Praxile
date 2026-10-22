import hashlib
import json

import pytest

from praxile.eval.contracts import BenchmarkOutcome, BenchmarkScore
from praxile.eval.five_arm_imports import analyze_imports
from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan, iter_schedule
from praxile.eval.v2.schema import EvalSchemaError


def fixture(tmp_path):
    plan = compile_plan({"study_id": "imports", "task_ids": ["task"], "heldout_task_ids": ["hold"],
        "repetitions": 1, "repeat_attempts": 2, "arm_order": list(ARMS),
        "identities": {key: "sha256:" + "a" * 64 for key in IDENTITIES},
        "budget": {"total_tokens": 100, "model_calls": 10, "wall_seconds": 10}})
    bindings = []
    for index, row in enumerate(iter_schedule(plan)):
        report = {"schema_version": "praxile.jobbench_import.v1", "task_checksum": "frozen",
            "outcome": BenchmarkOutcome("task", "completed", (
                BenchmarkScore("official_weighted_score", index, 10),)).to_dict(),
            "official_details": {"private_rubric": "must not be copied"}}
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(report))
        bindings.append({"cell_id": row["cell_id"], "path": str(path),
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "task_checksum": "frozen", "maximum": 10})
    return plan, bindings


def test_import_scores_are_extracted_without_copying_private_details(tmp_path):
    plan, bindings = fixture(tmp_path)
    result = analyze_imports(plan, bindings)
    assert result["status"] == "complete_declared_results"
    assert result["source_file_digests_verified"] is True
    assert result["comparisons"]["full_praxile"]["descriptive_delta"] == pytest.approx(0.4)
    assert "private_rubric" not in json.dumps(result)
    assert str(tmp_path) not in json.dumps(result)
    assert result["controlled_execution_verified"] is False
    assert result["promotion_eligible"] is False


@pytest.mark.parametrize("field,value", [("sha256", "changed"), ("task_checksum", "other"),
    ("maximum", 11), ("maximum", True), ("cell_id", "unplanned")])
def test_binding_changes_are_rejected(tmp_path, field, value):
    plan, bindings = fixture(tmp_path)
    bindings[0][field] = value
    with pytest.raises(EvalSchemaError):
        analyze_imports(plan, bindings)


def test_reusing_same_task_report_cannot_inflate_coverage(tmp_path):
    plan, bindings = fixture(tmp_path)
    bindings[1].update(path=bindings[0]["path"], sha256=bindings[0]["sha256"])
    with pytest.raises(EvalSchemaError, match="multiple cells"):
        analyze_imports(plan, bindings)


def test_changed_source_bytes_fail_hash_check(tmp_path):
    plan, bindings = fixture(tmp_path)
    (tmp_path / "0.json").write_text("{}")
    with pytest.raises(EvalSchemaError, match="digest mismatch"):
        analyze_imports(plan, bindings)


@pytest.mark.parametrize("defect", ["wrong_task", "mixed_schema", "missing_score", "unfinished"])
def test_semantic_source_validation_after_valid_hash(tmp_path, defect):
    plan, bindings = fixture(tmp_path)
    path = tmp_path / "1.json"
    report = json.loads(path.read_text())
    if defect == "wrong_task":
        report["outcome"]["task_id"] = "other"
    elif defect == "mixed_schema":
        report["schema_version"] = "praxile.workbuddy_import.v1"
    elif defect == "missing_score":
        report["outcome"]["scores"][0]["name"] = "self_reward"
    else:
        report["outcome"] = BenchmarkOutcome("task", "timed_out").to_dict()
    path.write_text(json.dumps(report))
    bindings[1]["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if defect == "unfinished":
        result = analyze_imports(plan, bindings)
        assert result["status"] == "incomplete"
        assert result["complete_task_count"] == 0
        assert result["comparisons"]["full_praxile"]["descriptive_delta"] is None
    else:
        with pytest.raises(EvalSchemaError):
            analyze_imports(plan, bindings)
