from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.runtime import AgentRuntime


def test_runtime_records_spec_compliance_before_reward(tmp_path: Path):
    (tmp_path / "spec.md").write_text(
        "# Feature\n\n"
        "## Problem Statement\nNeed safer parser output handling.\n\n"
        "## Success Metrics\n- Parser regression test passes\n\n"
        "## User Stories\n- Developer can parse model actions.\n\n"
        "## Acceptance Criteria\n- Parser strips fenced JSON.\n\n"
        "## Non-Goals\n- Do not change provider routing.\n\n"
        "## Constraints\n- Use existing parser module only.\n",
        encoding="utf-8",
    )
    config = Config.load(tmp_path)

    trajectory = AgentRuntime(config).run("Implement parser fenced JSON handling", max_steps=0, spec_files=["spec.md"])

    assert trajectory["spec_compliance"]["spec_files"] == ["spec.md"]
    assert trajectory["reward_report"]["spec_compliance"]["status"] in {"partial", "failed", "full"}
    assert trajectory["reward_report"]["objective_signals"]["spec_compliance_status"] is not None
