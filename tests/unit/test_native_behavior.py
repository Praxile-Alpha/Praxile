"""Offline behavioral descriptions never certify causal or quality improvement."""
from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from praxile.eval.native_behavior import compare_native_behavior, main, summarize_native_behavior
from praxile.eval.v2.schema import EvalSchemaError


def write_run(path, actions):
    path.write_text(json.dumps({"schema_version": "praxile.native_benchmark_run.v1",
        "experience_mode": "fresh_disabled", "trajectory": {
            "task_id": path.stem, "start_time": "2026-10-21T00:00:00Z", "actions": actions}}))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def pair(tmp_path):
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    action = {"step": 1, "action_type": "run_command", "input": {"command": "PRIVATE"},
              "status": "failure", "observation": {"data": {"exit_code": 1}}}
    left = write_run(baseline, [action, {**action, "step": 2}])
    right = write_run(candidate, [])
    return baseline, candidate, dict(baseline_sha256=left, candidate_sha256=right,
        development_task_ids=["baseline", "candidate"], heldout_task_ids=[])


def test_empty_candidate_is_not_a_success(pair):
    left, right, args = pair
    report = compare_native_behavior(left, right, **args)
    assert report["deltas"] == [{"operation": "run_command", "status": "failure",
        "baseline": 2, "candidate": 0, "candidate_minus_baseline": -2}]
    assert report["arms"]["candidate"]["timeline"] == []
    for flag in ("experimental_conditions_verified", "semantic_alignment_verified",
                 "causal_effect_verified", "task_success_verified", "promotion_eligible"):
        assert report[flag] is False
    assert "PRIVATE" not in json.dumps(report)


def test_unsupported_results_are_explicit_not_silently_counted_as_coverage(tmp_path):
    source = tmp_path / "unsupported.json"
    digest = write_run(source, [{"step": 1, "action_type": "future_unknown_tool",
        "input": {"payload": "PRIVATE"}, "status": "success",
        "observation": {"data": {"result": "PRIVATE"}}}])
    report = summarize_native_behavior(source, expected_sha256=digest,
        development_task_ids=[source.stem], heldout_task_ids=[])
    assert report["source_tool_result_count"] >= 1
    assert report["unprojected_tool_results"] == report["source_tool_result_count"]
    assert report["projected_count"] == 0
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("override", [
    {"heldout_task_ids": ["candidate"]}, {"candidate_sha256": "0" * 64},
    {"development_task_ids": ["baseline"]},
])
def test_pair_requires_both_sources_and_development_membership(pair, override):
    left, right, args = pair
    with pytest.raises(EvalSchemaError):
        compare_native_behavior(left, right, **{**args, **override})


def test_pair_cli_and_partial_arguments(pair, monkeypatch, capsys):
    left, right, args = pair
    argv = ["behavior", "--result", str(left), "--sha256", args["baseline_sha256"],
        "--development-task-id", "baseline", "--development-task-id", "candidate",
        "--candidate-result", str(right)]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    monkeypatch.setattr("sys.argv", argv + ["--candidate-sha256", args["candidate_sha256"]])
    main()
    assert json.loads(capsys.readouterr().out)["promotion_eligible"] is False


def test_public_behavior_receipt_binds_six_execution_sources():
    root = Path(__file__).resolve().parents[2] / "docs/results"
    receipt = json.loads((root / "docx-behavior-20261021.json").read_text())
    execution = json.loads((root / "docx-six-arm-20261020.json").read_text())
    assert receipt["independent_task_count"] == 1
    assert receipt["causal_effect_verified"] is receipt["promotion_eligible"] is False
    assert len(receipt["pairs"]) == 3
    for pair, source_pair in zip(receipt["pairs"], execution["pairs"], strict=True):
        assert pair["candidate"] == source_pair["candidate"]
        assert len(pair["arms"]) == 2
        for arm, source in zip(pair["arms"], source_pair["arms"], strict=True):
            assert arm["arm"] == source["arm"]
            report = arm["behavior"]
            assert report["source_result_sha256"] == source["source_sha256"]["praxile-result.json"]
            assert report["projected_count"] == len(report["timeline"]) == len(report["sources"])
            assert report["projected_count"] + report["unprojected_tool_results"] == report["source_tool_result_count"]
            counts = Counter((row["facts"]["operation"], row["facts"]["status"]) for row in report["timeline"])
            assert counts == {(row["operation"], row["status"]): row["count"] for row in report["operation_status_counts"]}
            assert [row["event_id"] for row in report["timeline"]] == [row["event_id"] for row in report["sources"]]
            assert report["task_success_verified"] is report["causal_effect_verified"] is False
