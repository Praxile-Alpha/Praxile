import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.model import OpenAICompatibleProvider, AnthropicProvider
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger


def test_providers_preserve_termination_metadata(monkeypatch):
    monkeypatch.setenv("FIXTURE_KEY", "fixture")
    transport = SimpleNamespace(post_json=Mock(return_value={"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}))
    provider = OpenAICompatibleProvider("fixture", {"base_url": "http://localhost", "api_key_env": "FIXTURE_KEY"}, transport)
    assert provider.chat({"model": "fixture", "messages": []})["finish_reason"] == "length"
    transport.post_json.return_value = {"content": [{"type": "text", "text": "{}"}], "stop_reason": "max_tokens"}
    provider = AnthropicProvider("fixture", {"api_key_env": "FIXTURE_KEY"}, transport)
    assert provider.chat({"model": "fixture", "messages": []})["finish_reason"] == "max_tokens"


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_truncated_valid_json_never_executes_and_cost_is_kept(tmp_path, reason):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.router = SimpleNamespace(chat=Mock(return_value={
        "content": json.dumps({"type": "edit_file", "path": "file.txt", "content": "partial"}),
        "finish_reason": reason, "usage": {"prompt_tokens": 10, "completion_tokens": 6000}}))
    runtime.tools = SimpleNamespace(execute=Mock())
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    logger = TrajectoryLogger("public task", {})
    messages = [{"role": "user", "content": "task"}]
    runtime._run_action_loop("public task", logger, [], max_steps=1,
        private=False, high_risk=False, messages=messages)
    runtime.tools.execute.assert_not_called()
    assert logger.data["cost"]["completion_tokens"] == 6000
    assert logger.data["actions"][0]["observation"]["data"]["failure_kind"] == "output_truncated"
    assert "was not executed" in messages[-1]["content"]


def test_parse_failure_feedback_explicitly_reports_no_execution(tmp_path):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.router = SimpleNamespace(chat=Mock(return_value={
        "content": '<think>' + 'x' * 5000 + '</think>{"type":}',
        "finish_reason": "stop", "usage": {"prompt_tokens": 5, "completion_tokens": 10}}))
    runtime.tools = SimpleNamespace(execute=Mock())
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    logger = TrajectoryLogger("public task", {})
    messages = [{"role": "user", "content": "task"}]
    runtime._run_action_loop("public task", logger, [], max_steps=1,
        private=False, high_risk=False, messages=messages)
    runtime.tools.execute.assert_not_called()
    data = logger.data['actions'][0]['observation']['data']
    assert data['content_preview_truncated'] is True
    assert data['decode_evidence']['reason'] == 'invalid_json_object'
    assert 'at 8' in data['decode_evidence']['parser_error']
    assert 'NO action was executed' in messages[-1]['content']
    assert logger.data['cost']['completion_tokens'] == 10
