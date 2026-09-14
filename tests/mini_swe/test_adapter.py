from __future__ import annotations

import sys
from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy, AdapterPolicyError, AdapterTask, MiniSweAgentAdapter


pytestmark = pytest.mark.mini_swe


def test_mini_swe_capabilities_are_honest_about_post_run_streaming() -> None:
    capabilities = MiniSweAgentAdapter(executable="definitely-not-installed-mini").capabilities()

    assert capabilities.event_streaming is False
    assert capabilities.artifact_collection is True
    assert capabilities.metadata["stream_mode"] == "post_run_trajectory"
    assert capabilities.metadata["available"] is False


def test_mini_swe_finds_console_script_beside_current_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interpreter = tmp_path / "bin" / "python"
    executable = interpreter.parent / "mini"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr("praxile.adapters.mini_swe.shutil.which", lambda _value: None)

    available, resolved = MiniSweAgentAdapter().availability()

    assert available is True
    assert resolved == str(executable.resolve())


def test_mini_swe_command_uses_resolved_console_script(tmp_path: Path) -> None:
    adapter = MiniSweAgentAdapter()
    command = adapter._command(
        AdapterTask("task_cmd", "Fix it", str(tmp_path)),
        AdapterPolicy(),
        tmp_path / "out.json",
        resolved_executable="/venv/bin/mini",
    )

    assert command[0] == "/venv/bin/mini"


def test_mini_swe_requires_unattended_isolated_policy(tmp_path: Path) -> None:
    adapter = MiniSweAgentAdapter()
    task = AdapterTask("task_policy", "Fix it", str(tmp_path))

    with pytest.raises(AdapterPolicyError, match="allow_unattended_execution"):
        adapter.run(task, AdapterPolicy())

    with pytest.raises(AdapterPolicyError, match="isolated workspace"):
        adapter.run(task, AdapterPolicy(settings={"allow_unattended_execution": True}))


def test_mini_swe_rejects_missing_root_without_creating_it(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    adapter = MiniSweAgentAdapter(command_prefix=[sys.executable])
    policy = AdapterPolicy(
        settings={"allow_unattended_execution": True, "workspace_isolated": True}
    )

    with pytest.raises(AdapterPolicyError, match="project root does not exist"):
        adapter.run(AdapterTask("task_missing", "Fix it", str(root)), policy)

    assert not root.exists()


def test_mini_swe_command_injects_context_and_budgets_without_shell(tmp_path: Path) -> None:
    adapter = MiniSweAgentAdapter(
        command_prefix=["python", "fake.py"], model="model-a", model_class="litellm_textbased"
    )
    task = AdapterTask("task_cmd", "Fix it", str(tmp_path))
    policy = AdapterPolicy(
        policy_id="candidate",
        context=({"asset_id": "skill_1", "content": "Run parser tests"},),
        budgets={"max_cost": 1.5},
        settings={"allow_unattended_execution": True, "workspace_isolated": True, "step_limit": 50},
    )

    command = adapter._command(task, policy, tmp_path / "out.json")

    assert command[:2] == ["python", "fake.py"]
    assert "<praxile_context" in command[command.index("--task") + 1]
    assert 'policy_version="1"' in command[command.index("--task") + 1]
    assert command[command.index("--model") + 1] == "model-a"
    assert command[command.index("--model-class") + 1] == "litellm_textbased"
    assert command[command.index("--cost-limit") + 1] == "1.5"
    config_values = [command[index + 1] for index, value in enumerate(command) if value == "--config"]
    assert config_values == ["mini.yaml", f"environment.cwd={tmp_path.resolve()}", "agent.step_limit=50"]
    assert command[command.index("--environment-class") + 1] == "local"
    instruction = command[command.index("--task") + 1]
    assert str(tmp_path.resolve()) in instruction
    assert "Do not search for or switch to /testbed" in instruction


def test_mini_swe_rejects_existing_non_yaml_config_file(tmp_path: Path) -> None:
    config = tmp_path / "mini.json"
    config.write_text("{}", encoding="utf-8")
    adapter = MiniSweAgentAdapter(command_prefix=["python"], config_specs=[str(config)])
    policy = AdapterPolicy(settings={"allow_unattended_execution": True, "workspace_isolated": True})

    with pytest.raises(AdapterPolicyError, match="must use the .yaml suffix"):
        adapter.run(AdapterTask("task_config", "Fix it", str(tmp_path)), policy)


def test_mini_swe_preflight_rejects_container_swebench_config_in_local_mode(tmp_path: Path) -> None:
    config = tmp_path / "swebench.yaml"
    config.write_text('environment:\n  cwd: "/testbed"\n', encoding="utf-8")
    adapter = MiniSweAgentAdapter(command_prefix=[sys.executable], config_specs=[str(config)])
    policy = AdapterPolicy(
        settings={"allow_unattended_execution": True, "workspace_isolated": True}
    )

    handle = adapter.run(AdapterTask("task_preflight", "Fix it", str(tmp_path)), policy)
    events = list(adapter.stream_events(handle))

    preflight = next(event for event in events if event.type == "PREFLIGHT")
    assert preflight.payload["status"] == "failed"
    assert "/testbed" in preflight.payload["blocking_reasons"][0]
    assert events[-2].payload["native_exit_status"] == "PreflightFailed"
    assert events[-1].payload["status"] == "failed"
    adapter.close()


def test_mini_swe_progress_distinguishes_tmp_scripts_from_repo_patch(tmp_path: Path) -> None:
    trajectory = {
        "messages": [
            {"role": "assistant", "extra": {"actions": [{"command": "pwd"}]}},
            {"role": "assistant", "extra": {"actions": [{"command": "cat > /tmp/repro.py"}]}},
            {"role": "assistant", "extra": {"actions": [{"command": "sed -i 's/a/b/' source.py"}]}},
        ]
    }
    patch = tmp_path / "workspace.patch"
    patch.write_text("diff --git a/source.py b/source.py\n", encoding="utf-8")

    progress = MiniSweAgentAdapter._progress(trajectory, tmp_path, patch)

    assert progress["environment_probe_count"] == 1
    assert progress["first_repo_write_action_heuristic"] == 3
    assert progress["patch_created"] is True
