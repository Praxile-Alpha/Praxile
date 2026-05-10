from __future__ import annotations

from pathlib import Path

from praxile.cli import main
from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.utils import utc_now


def test_spec_verify_updates_trajectory(tmp_path: Path):
    assert main(["--project", str(tmp_path), "init", "--no-detect"]) == 0
    (tmp_path / "spec.md").write_text(
        "# Feature\n\n"
        "## Problem Statement\nNeed parser repair.\n\n"
        "## Success Metrics\n- Regression test passes\n\n"
        "## User Stories\n- Developer can parse JSON actions.\n\n"
        "## Acceptance Criteria\n- Parser strips fenced JSON.\n\n"
        "## Non-Goals\n- Do not change model routing.\n\n"
        "## Constraints\n- Use existing parser module only.\n",
        encoding="utf-8",
    )
    config = Config.load(tmp_path)
    store = ExperienceStore(config.paths)
    store.initialize(config)
    now = utc_now()
    store.record_trajectory(
        {
            "task_id": "task_spec_verify",
            "user_task": "Fix parser fenced JSON",
            "start_time": now,
            "end_time": now,
            "actions": [
                {
                    "step": 1,
                    "action_type": "edit_file",
                    "input": {"path": "parser.py"},
                    "observation": {"output": "Parser strips fenced JSON."},
                    "status": "success",
                }
            ],
            "result": {"status": "completed", "summary": "Parser strips fenced JSON."},
            "spec_context": {"spec_files": ["spec.md"]},
            "reward_report": {"regression_passed": True, "notes": ["Configured tests/lint/build passed."]},
        }
    )

    assert main(["--project", str(tmp_path), "spec", "verify", "task_spec_verify"]) == 0
    updated = store.get_trajectory("task_spec_verify")
    assert updated is not None
    assert updated["spec_compliance"]["status"] in {"full", "partial"}
    assert updated["spec_compliance"]["satisfied"]
