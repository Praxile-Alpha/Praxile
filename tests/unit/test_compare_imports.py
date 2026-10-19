import json

import pytest

from praxile.eval.compare_imports import compare_imports, main
from praxile.eval.v2.schema import EvalSchemaError


def report(task, value, maximum=10, status="completed"):
    return {"schema_version": "praxile.jobbench_import.v1", "task_checksum": "checksum-" + task,
        "outcome": {"schema_version": "praxile.benchmark_outcome.v1", "task_id": task, "status": status,
            "scores": [{"name": "official_weighted_score", "value": value, "maximum": maximum}]
                      if status == "completed" else [], "evidence_refs": []}}


def test_normalizes_each_task_before_equal_weight_pairing():
    result = compare_imports([{"baseline": [report("a", 0, 10), report("b", 50, 100)],
                              "candidate": [report("a", 10, 10), report("b", 0, 100)]}])
    assert result["delta"]["estimate"] == 0.25
    assert result["sample_count"] == 2
    assert not result["promotion_eligible"]
    assert not result["controlled_experiment_verified"]


def test_repetitions_are_not_independent_samples_and_missing_judge_is_not_zero():
    pair = {"baseline": [report("a", 0)], "candidate": [report("a", 10)]}
    once = compare_imports([pair])
    repeated = compare_imports([pair] * 20)
    assert once["delta"] == repeated["delta"]
    assert repeated["sample_count"] == 1
    broken = {"baseline": [report("a", 0)], "candidate": [report("a", 0, status="judge_unavailable")]}
    result = compare_imports([pair, broken])
    assert result["sample_count"] == 0
    assert result["delta"]["estimate"] is None
    assert result["tasks"][0]["pairs"][1]["candidate"]["normalized"] is None


@pytest.mark.parametrize("change", ["checksum", "denominator", "duplicate"])
def test_changed_contract_or_duplicate_task_is_rejected(change):
    a, b = report("a", 0), report("a", 1)
    if change == "checksum":
        b["task_checksum"] = "different"
    elif change == "denominator":
        b["outcome"]["scores"][0]["maximum"] = 20
    candidate = [b, b] if change == "duplicate" else [b]
    with pytest.raises(EvalSchemaError):
        compare_imports([{"baseline": [a], "candidate": candidate}])


def test_cli_records_input_hashes_without_private_judge_details(tmp_path, monkeypatch):
    a, b = report("a", 0), report("a", 5)
    b["official_details"] = {"secret_rubric": "PRIVATE_RUBRIC"}
    for name, value in (("a.json", a), ("b.json", b)):
        (tmp_path / name).write_text(json.dumps(value))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"schema_version": "praxile.import_comparison_plan.v1",
        "repetitions": [{"baseline": ["a.json"], "candidate": ["b.json"]}]}))
    output = tmp_path / "comparison.json"
    monkeypatch.setattr("sys.argv", ["compare", "--manifest", str(plan), "--output", str(output)])
    main()
    result = json.loads(output.read_text())
    assert len(result["inputs"]) == 2
    assert result["plan_digest"].startswith("sha256:")
    assert "PRIVATE_RUBRIC" not in output.read_text()
    monkeypatch.setattr("sys.argv", ["compare", "--manifest", str(plan), "--output", str(tmp_path / "a.json")])
    with pytest.raises(SystemExit):
        main()
