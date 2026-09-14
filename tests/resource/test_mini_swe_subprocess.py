from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy, AdapterRunner, AdapterTask, MiniSweAgentAdapter
from praxile.config import Config
from praxile.trace import EventStore


pytestmark = [pytest.mark.resource, pytest.mark.shell_resource, pytest.mark.sqlite_resource]


_FAKE_MINI = r'''from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--task")
parser.add_argument("--output")
parser.add_argument("--yolo", action="store_true")
parser.add_argument("--exit-immediately", action="store_true")
parser.add_argument("--model")
parser.add_argument("--model-class")
parser.add_argument("--cost-limit")
parser.add_argument("--config", action="append")
parser.add_argument("--environment-class")
args = parser.parse_args()
assert os.environ["MSWEA_CONFIGURED"] == "true"
assert os.environ["MSWEA_SILENT_STARTUP"] == "1"
assert os.environ["MSWEA_GLOBAL_CONFIG_DIR"].endswith("mini-config")
payload = {
    "trajectory_format": "mini-swe-agent-1.1",
    "info": {
        "mini_version": "2.fixture",
        "exit_status": "Submitted",
        "submission": "fixed parser",
        "model_stats": {"instance_cost": 0.03, "api_calls": 1},
    },
    "messages": [
        {"role": "system", "content": "system"},
        {"role": "user", "content": args.task},
        {
            "role": "user",
            "content": "Format error: expected one action",
            "extra": {
                "interrupt_type": "FormatError",
                "model_response": "invalid response",
                "cost": 0.01,
                "response": {"usage": {"prompt_tokens": 3, "completion_tokens": 2}},
            },
        },
        {
            "role": "assistant",
            "content": "Inspect tests",
            "extra": {
                "cost": 0.03,
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                "actions": [{"id": "native_call_1", "command": "pytest -q"}],
            },
        },
        {
            "role": "tool",
            "content": "{\"returncode\": 0, \"output\": \"1 passed\"}",
            "tool_call_id": "native_call_1",
            "extra": {"raw_output": "x" * 100000},
        },
        {"role": "exit", "content": "Submitted", "extra": {"exit_status": "Submitted", "submission": "fixed parser"}},
    ],
}
Path(args.output).write_text(json.dumps(payload), encoding="utf-8")
print("fake mini complete")
'''


def test_mini_swe_subprocess_is_translated_and_committed(tmp_path: Path) -> None:
    fake = tmp_path / "fake_mini.py"
    fake.write_text(_FAKE_MINI, encoding="utf-8")
    adapter = MiniSweAgentAdapter(
        command_prefix=[sys.executable, str(fake)],
        model="fixture/model",
        model_class="litellm_textbased",
    )
    runner = AdapterRunner(EventStore(Config.load(tmp_path).paths))
    policy = AdapterPolicy(
        policy_id="candidate",
        context=({"asset_id": "skill_parser", "content": "Run focused parser tests"},),
        budgets={"max_cost": 2.0, "wall_timeout_seconds": 10},
        settings={
            "allow_unattended_execution": True,
            "workspace_isolated": True,
            "verification_commands": ["pytest -q"],
        },
    )

    result = runner.execute(adapter, AdapterTask("task_mini", "Fix parser", str(tmp_path)), policy)

    event_types = [event.type for event in result.events]
    assert event_types[0] == "RUN_START"
    assert "CONTEXT_INJECT" in event_types
    assert "MODEL_CALL" in event_types
    assert "TOOL_CALL" in event_types
    assert "TOOL_RESULT" in event_types
    assert "VERIFICATION" in event_types
    assert "PREFLIGHT" in event_types
    assert "PROGRESS" in event_types
    assert event_types[-1] == "RUN_END"
    model_event = next(event for event in result.events if event.type == "MODEL_CALL")
    assert model_event.payload["parse_status"] == "rejected"
    assert model_event.token_usage is not None
    assert model_event.token_usage.input == 3
    successful_model_event = next(
        event for event in result.events if event.type == "MODEL_CALL" and "parse_status" not in event.payload
    )
    assert successful_model_event.token_usage is not None
    assert successful_model_event.token_usage.input == 12
    assert successful_model_event.cost == 0.03
    verification = next(event for event in result.events if event.type == "VERIFICATION")
    assert verification.payload["status"] == "passed"
    tool_result = next(event for event in result.events if event.type == "TOOL_RESULT")
    native_extra = tool_result.payload["native_message"]["extra"]
    assert "raw_output" not in native_extra
    assert native_extra["raw_output_meta"]["original_chars"] == 100000
    assert len(tool_result.to_json().encode("utf-8")) < 10_000
    assert {item.type for item in result.artifacts} == {
        "adapter_preflight",
        "native_trajectory",
        "process_stdout",
    }
    replay = runner.event_store.replay(result.handle.trace_id)
    assert replay["runs"][0]["status"] == "completed"
    assert len(replay["artifacts"]) == 3
    native_trajectory = next(item for item in result.artifacts if item.type == "native_trajectory")
    native_path = tmp_path / native_trajectory.uri.removeprefix("project://")
    assert "x" * 100000 in native_path.read_text(encoding="utf-8")
    adapter.close()


def test_stopping_policy_terminates_running_subprocess(tmp_path: Path) -> None:
    fake = tmp_path / "incremental_mini.py"
    fake.write_text(
        """import argparse, json, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--output')
p.add_argument('--task')
p.add_argument('--yolo', action='store_true')
p.add_argument('--exit-immediately', action='store_true')
p.add_argument('--config', action='append')
p.add_argument('--environment-class')
a, _ = p.parse_known_args()
messages = []
for i in range(2):
    messages += [
        {'role': 'assistant', 'extra': {'actions': [{'command': f'echo {i}'}]}},
        {'role': 'tool', 'content': '<returncode>0</returncode>'},
    ]
    Path(a.output).write_text(json.dumps({'messages': messages, 'info': {}}))
    time.sleep(0.3)
time.sleep(30)
""",
        encoding="utf-8",
    )
    adapter = MiniSweAgentAdapter(command_prefix=[sys.executable, str(fake)])
    policy = AdapterPolicy(
        settings={
            "allow_unattended_execution": True,
            "workspace_isolated": True,
            "stopping_policy": {
                "enabled": True,
                "max_steps_without_patch": 2,
                "poll_interval_seconds": 0.1,
            },
        },
        budgets={"wall_timeout_seconds": 10},
    )

    result = AdapterRunner(EventStore(Config.load(tmp_path).paths)).execute(
        adapter,
        AdapterTask("task_stop", "Investigate", str(tmp_path)),
        policy,
    )

    stop = next(event for event in result.events if event.type == "STOP_DECISION")
    assert stop.payload["reason"] == "exploration_budget_exhausted"
    final = next(event for event in result.events if event.type == "FINAL_RESULT")
    assert final.payload["status"] == "policy_stopped"
    adapter.close()


def test_mini_swe_cancel_terminates_process_group(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep_mini.py"
    sleeper.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    adapter = MiniSweAgentAdapter(command_prefix=[sys.executable, str(sleeper)])
    policy = AdapterPolicy(
        settings={"allow_unattended_execution": True, "workspace_isolated": True},
        budgets={"wall_timeout_seconds": 5},
    )
    handle = adapter.run(AdapterTask("task_cancel", "Wait", str(tmp_path)), policy)
    process = adapter._runs[handle.native_run_id].process

    adapter.cancel(handle)
    events = list(adapter.stream_events(handle))

    assert process.poll() is not None
    assert events[-1].payload["status"] == "cancelled"
    adapter.close()


def test_workspace_patch_includes_untracked_source_but_not_native_evidence(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Praxile Tests"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("NEW = True\n", encoding="utf-8")
    native = tmp_path / ".praxile" / "trace" / "native" / "run"
    native.mkdir(parents=True)
    (native / "trajectory.json").write_text("{}", encoding="utf-8")
    patch_path = native / "workspace.patch"

    MiniSweAgentAdapter._capture_workspace_patch(tmp_path, patch_path)

    patch = patch_path.read_text(encoding="utf-8")
    assert "tracked.py" in patch
    assert "new.py" in patch
    assert ".praxile/trace/native" not in patch
