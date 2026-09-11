from __future__ import annotations

from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy, AdapterPolicyError, AdapterTask, MiniSweAgentAdapter


def test_mini_swe_capabilities_are_honest_about_post_run_streaming() -> None:
    capabilities = MiniSweAgentAdapter(executable="definitely-not-installed-mini").capabilities()

    assert capabilities.event_streaming is False
    assert capabilities.artifact_collection is True
    assert capabilities.metadata["stream_mode"] == "post_run_trajectory"
    assert capabilities.metadata["available"] is False


def test_mini_swe_requires_unattended_isolated_policy(tmp_path: Path) -> None:
    adapter = MiniSweAgentAdapter()
    task = AdapterTask("task_policy", "Fix it", str(tmp_path))

    with pytest.raises(AdapterPolicyError, match="allow_unattended_execution"):
        adapter.run(task, AdapterPolicy())

    with pytest.raises(AdapterPolicyError, match="isolated workspace"):
        adapter.run(task, AdapterPolicy(settings={"allow_unattended_execution": True}))


def test_mini_swe_command_injects_context_and_budgets_without_shell(tmp_path: Path) -> None:
    adapter = MiniSweAgentAdapter(
        command_prefix=["python", "fake.py"], model="model-a", model_class="litellm_textbased"
    )
    task = AdapterTask("task_cmd", "Fix it", str(tmp_path))
    policy = AdapterPolicy(
        policy_id="candidate",
        context=({"asset_id": "skill_1", "content": "Run parser tests"},),
        budgets={"max_cost": 1.5},
        settings={"allow_unattended_execution": True, "workspace_isolated": True},
    )

    command = adapter._command(task, policy, tmp_path / "out.json")

    assert command[:2] == ["python", "fake.py"]
    assert "<praxile_context" in command[command.index("--task") + 1]
    assert command[command.index("--model") + 1] == "model-a"
    assert command[command.index("--model-class") + 1] == "litellm_textbased"
    assert command[command.index("--cost-limit") + 1] == "1.5"


def test_mini_swe_rejects_existing_non_yaml_config_file(tmp_path: Path) -> None:
    config = tmp_path / "mini.json"
    config.write_text("{}", encoding="utf-8")
    adapter = MiniSweAgentAdapter(command_prefix=["python"], config_specs=[str(config)])
    policy = AdapterPolicy(settings={"allow_unattended_execution": True, "workspace_isolated": True})

    with pytest.raises(AdapterPolicyError, match="must use the .yaml suffix"):
        adapter.run(AdapterTask("task_config", "Fix it", str(tmp_path)), policy)
