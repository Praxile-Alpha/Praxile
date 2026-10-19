import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime
from praxile.task_notes import add_note, select_notes
from praxile.trajectory import TrajectoryLogger


def source():
    logger = TrajectoryLogger("task", {})
    episode_id = logger.begin_subtask("inspect", {})
    logger.add_action(action_type="read_file", input_data={}, observation={"output": "evidence"}, status="success")
    logger.end_subtask(claimed_outcome="completed", summary="observed", snapshot={})
    action = {"type": "subtask_note", "source_episode_id": episode_id, "note_text": "UNIQUE_HYPOTHESIS",
              "ttl_steps": 1, "requirements": {"state_equals": {"git.is_repo": True}}}
    return logger, action


@pytest.mark.parametrize("field,value", [("ttl_steps", True), ("ttl_steps", 0), ("ttl_steps", 21),
    ("note_text", ""), ("source_episode_id", "other"), ("requirements", {})])
def test_note_rejects_invalid_source_bounds_or_conditions(field, value):
    logger, action = source()
    action[field] = value
    with pytest.raises(ValueError):
        add_note(logger.data, action, {"state": {"git": {"is_repo": True}}})


def test_note_unknown_state_invalidates_permanently():
    logger, action = source()
    add_note(logger.data, action, {"state": {"git": {"is_repo": True}}})
    assert not select_notes(logger.data, {})
    assert logger.data["task_notes"][0]["status"] == "invalidated"
    assert not select_notes(logger.data, {"state": {"git": {"is_repo": True}}})


def test_action_loop_injects_note_once_without_retaining_body_in_history(tmp_path, monkeypatch):
    logger, note_action = source()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.project = SimpleNamespace(snapshot=lambda **kwargs: {"git": {"is_repo": True}})
    runtime.tools = SimpleNamespace(
        safety=SimpleNamespace(check_tool_call=lambda *args, **kwargs: SimpleNamespace(allowed=True)),
        execute=Mock(return_value={"status": "success", "output": "observed", "data": {}}))
    responses = iter([note_action, {"type": "read_file", "path": "a.py"}, {"type": "finish", "summary": "done"}])
    prompts = []
    def chat(messages, **kwargs):
        prompts.append(json.dumps(messages))
        return {"content": json.dumps(next(responses)), "usage": {}}
    runtime.router = SimpleNamespace(chat=chat)
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._refresh_snapshot_after_action = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    monkeypatch.setattr("praxile.runtime.observe_tool_versions", lambda: {})
    history = [{"role": "user", "content": "task"}]
    status, _ = runtime._run_action_loop("task", logger, [], max_steps=3, private=False, high_risk=False,
                                        messages=history)
    assert status == "completed"
    assert ["UNIQUE_HYPOTHESIS" in prompt for prompt in prompts] == [False, True, False]
    assert "UNIQUE_HYPOTHESIS" not in json.dumps(history)
    assert logger.data["task_notes"][0]["status"] == "expired"
    assert logger.data["task_note_usage"][0]["note_ids"]
    assert not logger.data["task_note_usage"][1]["note_ids"]
    assert logger.data["cost"]["tool_calls"] == 2
    restored = TrajectoryLogger.from_data(json.loads(json.dumps(logger.data)))
    assert restored.data["task_notes"] == logger.data["task_notes"]
