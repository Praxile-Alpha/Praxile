import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime
from praxile.security import SafetyPolicy
from praxile.trajectory import TrajectoryLogger


def test_subtask_evidence_resume_and_claim_separation():
    logger = TrajectoryLogger("task", {})
    state = {"files": ["before"]}
    identity = logger.begin_subtask("deliver", state)
    state["files"].append("mutated")
    logger.add_action(action_type="run_command", input_data={}, observation={"data": {"artifact": "output/a"}}, status="failure")
    restored = TrajectoryLogger.from_data(json.loads(json.dumps(logger.data)))
    assert restored.end_subtask(claimed_outcome="completed", summary="model claim", snapshot={"files": ["after"]}) == identity
    episode = restored.data["subtask_episodes"][0]
    assert episode["state_before"] == {"files": ["before"]}
    assert episode["status"] == "closed"
    assert episode["claimed_outcome"] == "completed"
    assert episode["observed_status_counts"] == {"failure": 1}
    assert episode["action_refs"] == [restored.data["actions"][0]["action_id"]]
    assert episode["artifacts"] == ["output/a"]
    assert episode["uncertainty"]


def test_unclosed_subtask_is_not_marked_successful():
    logger = TrajectoryLogger("task", {})
    logger.begin_subtask("deliver", {})
    with pytest.raises(ValueError):
        logger.begin_subtask("nested", {})
    logger.finish(status="completed", summary="model finished")
    episode = logger.data["subtask_episodes"][0]
    assert episode["status"] == "interrupted"
    assert episode["state_after"] is None
    assert episode["claimed_outcome"] is None


def test_runtime_records_explicit_boundaries_and_tool_references(tmp_path):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    actions = [{"type": "subtask_begin", "goal": "inspect"}, {"type": "list_files"},
               {"type": "subtask_end", "claimed_outcome": "completed", "summary": "inspection done"}]
    runtime.router = SimpleNamespace(chat=Mock(side_effect=[{"content": json.dumps(action)} for action in actions]))
    runtime.tools = SimpleNamespace(safety=SafetyPolicy(runtime.config),
        execute=Mock(return_value={"status": "success", "output": "files", "data": {}}))
    runtime.project = SimpleNamespace(snapshot=Mock(side_effect=[{"state": "before"}, {"state": "after"}]))
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    logger = TrajectoryLogger("task", {})
    runtime._run_action_loop("task", logger, [], max_steps=3, private=False, high_risk=False,
        messages=[{"role": "user", "content": "task"}])
    episode = logger.data["subtask_episodes"][0]
    assert episode["state_before"] == {"state": "before"}
    assert episode["state_after"] == {"state": "after"}
    assert episode["action_refs"] == [logger.data["actions"][1]["action_id"]]
    assert logger.data["cost"]["tool_calls"] == 1
    assert runtime.tools.execute.call_count == 1
    assert runtime._write_checkpoint.call_count == 3
