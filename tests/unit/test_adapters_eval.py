from __future__ import annotations

import json
from pathlib import Path

from praxile.adapters import GenericJSONLAdapter
from praxile.cli import main
from praxile.config import Config
from praxile.eval import EvalRunner, EvalSuite
from praxile.store import ExperienceStore


def test_generic_jsonl_adapter_normalizes_external_trace(tmp_path: Path) -> None:
    trace = tmp_path / "external.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"event": "task", "task_id": "external_task", "task": "Fix timeout retry"}),
                json.dumps({"event": "action", "tool": "run_command", "command": "python -m pytest", "status": "success"}),
                json.dumps({"event": "observation", "output": "1 passed", "status": "success"}),
                json.dumps({"event": "result", "status": "completed", "summary": "Fixed."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    imported = GenericJSONLAdapter().import_file(trace)

    trajectory = imported["trajectory"]
    assert trajectory["task_id"] == "external_task"
    assert trajectory["user_task"] == "Fix timeout retry"
    assert trajectory["actions"][0]["action_type"] == "run_command"
    assert trajectory["external_adapter"]["adapter"] == "generic_jsonl"


def test_cli_import_jsonl_records_trajectory(tmp_path: Path) -> None:
    trace = tmp_path / "external.jsonl"
    trace.write_text(
        json.dumps({"event": "task", "task_id": "external_cli", "task": "Import external trace"}) + "\n",
        encoding="utf-8",
    )

    assert main(["--project", str(tmp_path), "interop", "import-jsonl", str(trace)]) == 0

    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    assert store.get_trajectory("external_cli")["user_task"] == "Import external trace"


def test_eval_runner_scores_generated_proposals(tmp_path: Path) -> None:
    config = Config.load(tmp_path)
    config.data["proposal_gate"]["enabled"] = False
    store = ExperienceStore(config.paths)
    store.initialize(config)
    trajectory = {
        "task_id": "task_eval_failure",
        "user_task": "Fix pytest timeout retry failure",
        "start_time": "2026-05-15T00:00:00+00:00",
        "end_time": "2026-05-15T00:00:01+00:00",
        "environment_snapshot": {},
        "actions": [
            {
                "step": 1,
                "action_type": "run_command",
                "status": "failure",
                "input": {"command": "python -m pytest tests/test_retry.py"},
                "observation": {"output": "TimeoutError while retry backoff failed", "status": "failure"},
            }
        ],
        "reward_report": {
            "overall": 0.3,
            "should_generate_experience": True,
            "experience_generation": {"should_generate_experience": True, "signals": {"failures": True}},
            "test_results": [],
        },
        "result": {"status": "failed", "summary": "Retry test failed."},
    }
    trajectory_path = tmp_path / "trajectory.json"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "failure proposal eval",
                "cases": [
                    {
                        "name": "extract retry failure pattern",
                        "input": {"trajectory_file": "trajectory.json"},
                        "expected": {"proposal_type": "failure_pattern", "keywords": ["pytest", "timeout"]},
                        "metrics": ["proposal_type_match", "keyword_hit"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = EvalRunner(config, store).run(EvalSuite.load(suite_path))

    assert report["passed"] is True
    assert report["average_score"] == 1.0
    assert report["cases"][0]["generated_count"] >= 1


def test_cli_eval_run_writes_report(tmp_path: Path) -> None:
    trajectory = {
        "task_id": "task_eval_cli",
        "user_task": "Remember verification command",
        "actions": [
            {
                "step": 1,
                "action_type": "run_command",
                "status": "success",
                "input": {"command": "python -m pytest"},
                "observation": {"output": "passed"},
            }
        ],
        "reward_report": {
            "should_generate_experience": True,
            "experience_generation": {"should_generate_experience": True, "signals": {"memory_requested": True}},
        },
        "result": {"status": "completed", "summary": "Done."},
    }
    (tmp_path / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "cases": [
                        {
                                "name": "memory proposal",
                                "input": {"trajectory_file": "trajectory.json"},
                                "expected": {"proposal_type": "memory_update"},
                            }
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    config = Config.load(tmp_path)
    config.data["proposal_gate"]["enabled"] = False
    config.write()

    assert main(["--project", str(tmp_path), "eval", "run", str(suite), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["case_count"] == 1
    assert report["passed"] is True
