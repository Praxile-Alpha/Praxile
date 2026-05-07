from __future__ import annotations
import tempfile
from pathlib import Path
import pytest
from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.store import ExperienceStore

pytestmark = pytest.mark.resource

@pytest.mark.runtime_resource
@pytest.mark.sqlite_resource
def test_ui_task_generates_skill_eval_harness_rule_and_routing_proposals() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text('{"scripts":{"test":"echo ok"}}\n', encoding="utf-8")
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        trajectory = AgentRuntime(config).run("修复按钮选中态反馈", max_steps=1)
        proposal_types = {item["type"] for item in trajectory["experience_candidates"]}
        assert "skill_create" in proposal_types
        assert "eval_case" in proposal_types
        assert "harness_rule" in proposal_types
        assert "routing" in proposal_types
        assert trajectory["reward_report"]["signals"]["ui_sensitive"]

@pytest.mark.runtime_resource
@pytest.mark.sqlite_resource
def test_architecture_task_stops_at_gate_and_proposes_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        trajectory = AgentRuntime(config).run("修改 auth session schema", max_steps=1)
        proposal_types = {item["type"] for item in trajectory["experience_candidates"]}
        assert trajectory["actions"][0]["action_type"] == "architecture_gate"
        assert trajectory["actions"][0]["observation"]["data"]["implementation_blocked"]
        assert "edit_file" in trajectory["actions"][0]["observation"]["data"]["blocked_action_types"]
        assert "architecture_gate" in proposal_types
        assert "frozen_boundary" in proposal_types
        assert "harness_rule" in proposal_types
        assert trajectory["reward_report"]["objective_signals"]["architecture_gate_triggered"]

@pytest.mark.runtime_resource
@pytest.mark.sqlite_resource
def test_trajectory_writes_external_compat_sidecar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        trajectory = AgentRuntime(config).run("记录项目上下文", max_steps=1)
        sidecar = root / ".praxile" / "experience" / "trajectories" / "external_compat.jsonl"
        assert sidecar.exists()
        text = sidecar.read_text(encoding="utf-8")
        assert trajectory["task_id"] in text
        assert trajectory["schema"] == "praxile_trajectory_v1"
        assert trajectory["external_compat"]["format"] == "sharegpt_jsonl_sidecar_v1"
