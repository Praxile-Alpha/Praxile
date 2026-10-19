import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime
from praxile.task_notes import add_note, select_notes
from praxile.subtask_reflection import reflect_subtask
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


@pytest.mark.parametrize("use_reflector", [False, True])
def test_action_loop_injects_note_once_without_retaining_body_in_history(tmp_path, monkeypatch, use_reflector):
    logger, note_action = source()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.project = SimpleNamespace(snapshot=lambda **kwargs: {"git": {"is_repo": True}})
    runtime.tools = SimpleNamespace(
        safety=SimpleNamespace(check_tool_call=lambda *args, **kwargs: SimpleNamespace(allowed=True)),
        execute=Mock(return_value={"status": "success", "output": "observed", "data": {}}))
    if use_reflector:
        note_action = {key: value for key, value in note_action.items() if key != "note_text"}
        note_action["type"] = "subtask_reflect"
    responses = iter([note_action, {"type": "read_file", "path": "a.py"}, {"type": "finish", "summary": "done"}])
    prompts = []
    def chat(messages, **kwargs):
        if kwargs["purpose"] == "experience_reflection":
            return {"content": json.dumps({"decision": "note", "note_text": "UNIQUE_HYPOTHESIS",
                "evidence_refs": [logger.data["actions"][0]["action_id"]]}), "usage": {"input_tokens": 12}}
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
    assert logger.data["cost"]["model_calls"] == (4 if use_reflector else 3)


@pytest.mark.parametrize("decision", ["abstain", "bad_reference", "bad_json"])
def test_reflector_abstention_and_invalid_output_do_not_create_notes(decision):
    logger, action = source()
    content = json.dumps({"decision": "abstain"}) if decision == "abstain" else (
        "not json" if decision == "bad_json" else json.dumps({"decision": "note",
            "note_text": "unsupported", "evidence_refs": ["invented"]}))
    router = SimpleNamespace(chat=Mock(return_value={"content": content, "usage": {"input_tokens": 12}}))
    if decision == "abstain":
        result = reflect_subtask(logger, action, {"state": {"git": {"is_repo": True}}}, router, private=True)
        assert result["data"]["decision"] == "abstain"
    else:
        with pytest.raises(ValueError):
            reflect_subtask(logger, action, {"state": {"git": {"is_repo": True}}}, router, private=True)
    assert not logger.data.get("task_notes")
    assert logger.data["cost"]["prompt_tokens"] == 12
    assert router.chat.call_args.kwargs["private"] is True
    with pytest.raises(ValueError, match="attempt limit"):
        reflect_subtask(logger, action, {"state": {"git": {"is_repo": True}}}, router)
    assert router.chat.call_count == 1
