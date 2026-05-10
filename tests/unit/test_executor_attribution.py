from __future__ import annotations

from pathlib import Path

from praxile.config import Config
from praxile.evidence import EvidenceExtractor
from praxile.evolution import EvolutionEngine
from praxile.reward import RewardEngine
from praxile.runtime import AgentRuntime
from praxile.tools import ToolRegistry


def test_batch_readonly_records_executor_events(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = Config.load(tmp_path)
    tools = ToolRegistry(config, fs=None, shell=None, tests=None)
    try:
        observation = tools.execute(
            {
                "type": "batch",
                "actions": [
                    {"type": "list_files"},
                    {"type": "read_file", "path": "app.py"},
                ],
            },
            task_id="task_exec",
            step=1,
        )
    finally:
        tools.close()

    assert observation["status"] == "success"
    data = observation["data"]
    assert data["concurrent"] is True
    assert data["executor"]["kind"] == "parallel_readonly_coordinator"
    assert len(data["executor_events"]) == 2
    assert data["observations"][1]["data"]["executor"]["kind"] == "readonly_worker"


def test_runtime_parallel_readonly_exploration_is_attributed(tmp_path: Path):
    (tmp_path / "parser.py").write_text("def parse(value):\n    return value\n", encoding="utf-8")
    config = Config.load(tmp_path)
    config.data.setdefault("executors", {})["parallel_readonly_exploration_enabled"] = True

    trajectory = AgentRuntime(config).run("Inspect parser behavior", max_steps=0)

    executor_ids = {item["executor_id"] for item in trajectory["executors"]}
    assert "coding_agent" in executor_ids
    assert "parallel_readonly" in executor_ids
    assert any(item.startswith("readonly_explorer_") for item in executor_ids)
    exploration = trajectory["parallel_readonly_exploration"]
    assert exploration["enabled"] is True
    action = next(item for item in trajectory["actions"] if item["action_type"] == "parallel_readonly_exploration")
    assert action["executor"]["executor_id"] == "parallel_readonly"
    assert action["observation"]["data"]["concurrent"] is True


def test_executor_attribution_flows_into_evidence_and_memory(tmp_path: Path):
    config = Config.load(tmp_path)
    trajectory = {
        "task_id": "task_exec_memory",
        "user_task": "record this parser investigation",
        "result": {"status": "completed"},
        "parallel_readonly_exploration": {"enabled": True, "action_count": 2},
        "executors": [
            {"executor_id": "coding_agent", "kind": "agent_runtime", "role": "coding_agent"},
            {"executor_id": "parallel_readonly", "kind": "parallel_readonly_coordinator", "role": "pre_model_exploration"},
        ],
        "actions": [
            {
                "action_type": "parallel_readonly_exploration",
                "status": "success",
                "executor": {"executor_id": "parallel_readonly", "kind": "parallel_readonly_coordinator"},
                "observation": {
                    "data": {
                        "count": 2,
                        "executor_events": [
                            {"executor_id": "readonly_explorer_1", "kind": "readonly_worker", "role": "project_map"},
                            {"executor_id": "readonly_explorer_2", "kind": "readonly_worker", "role": "search"},
                        ],
                        "observations": [{"status": "success"}, {"status": "success"}],
                    }
                },
            },
            {
                "action_type": "finish",
                "status": "success",
                "executor": {"executor_id": "coding_agent", "kind": "agent_runtime"},
            },
        ],
    }
    trajectory["reward_report"] = RewardEngine(config).build_report(trajectory, [])

    evidence = EvidenceExtractor.extract(trajectory)
    assert evidence["executor_attribution"]["quality"] == "complete"
    assert evidence["executor_attribution"]["parallel_readonly"]["worker_count"] == 2

    proposals = EvolutionEngine(config).generate(trajectory)
    memory = next(item for item in proposals if item["type"] == "memory_update")
    content = memory["changes"][0]["content"]
    assert "### Executor Attribution" in content
    assert "Parallel read-only exploration" in content
