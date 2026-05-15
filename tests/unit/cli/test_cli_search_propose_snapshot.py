from __future__ import annotations

from pathlib import Path

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_cli_search_snapshot_and_snapshot_rollback(tmp_path: Path, capsys) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    memory = config.paths.state / "memory" / "api.md"
    memory.write_text("# API Memory\n\nUse retry with timeout backoff for flaky API calls.\n", encoding="utf-8")
    store.index_asset(memory)
    config.write()

    assert main(["--project", str(tmp_path), "search", "retry timeout backoff"]) == 0
    output = capsys.readouterr().out
    assert ".praxile/memory/api.md" in output

    assert main(["--project", str(tmp_path), "snapshot", "create", "--reason", "before edit"]) == 0
    created = capsys.readouterr().out
    snapshot_id = created.split()[2]
    memory.write_text("mutated\n", encoding="utf-8")

    assert main(["--project", str(tmp_path), "snapshot", "list"]) == 0
    assert snapshot_id in capsys.readouterr().out

    assert main(["--project", str(tmp_path), "rollback", snapshot_id]) == 0
    assert "Use retry with timeout backoff" in memory.read_text(encoding="utf-8")


def test_cli_propose_generates_pending_proposals_from_trajectory(tmp_path: Path, capsys) -> None:
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    config.write()
    store.record_trajectory(
        {
            "task_id": "task_propose",
            "user_task": "record this parser failure repair",
            "start_time": utc_now(),
            "end_time": utc_now(),
            "result": {"status": "completed", "summary": "Parser repair completed."},
            "reward_report": {
                "overall": 0.8,
                "should_generate_experience": True,
                "experience_generation": {
                    "signals": {"memory_requested": True},
                    "evidence_strength": "medium",
                    "reason": "manual test trajectory",
                },
            },
            "actions": [
                {
                    "step": 1,
                    "action_type": "run_command",
                    "status": "success",
                    "observation": {"data": {"command": "python -m pytest tests/test_parser.py"}, "output": "passed"},
                }
            ],
        }
    )

    assert main(["--project", str(tmp_path), "propose", "task_propose"]) == 0
    output = capsys.readouterr().out
    assert "pending proposal" in output
    assert store.list_proposals(status="pending")
