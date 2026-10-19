import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger


@pytest.mark.parametrize("second_state", [False, None])
@pytest.mark.parametrize("condition_path", ["git.is_repo", "browser.dom.ready"])
def test_loop_stops_before_next_model_request_when_conditions_expire(tmp_path, monkeypatch, second_state, condition_path):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.project = SimpleNamespace(snapshot=Mock(side_effect=[
        {"git": {"is_repo": True}}, {"git": {"is_repo": second_state}}]))
    monkeypatch.setattr("praxile.runtime.observe_tool_versions", lambda: {})
    runtime.router = SimpleNamespace(chat=Mock(return_value={"content": json.dumps(
        {"type": "read_file", "path": "sample.py"}), "usage": {}}))
    runtime.tools = SimpleNamespace(execute=Mock(return_value={"status": "success", "output": "ok", "data": {}}))
    if condition_path.startswith("browser"):
        runtime.tools.browser = SimpleNamespace(observe_state=Mock(side_effect=[
            {"dom": {"ready": True}}, {"dom": {"ready": second_state}}]))
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._refresh_snapshot_after_action = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    logger = TrajectoryLogger("public task", {})
    selected = [{"path": "memory.md", "activation_requirements": {"state_equals": {condition_path: True}}}]
    status, summary = runtime._run_action_loop("public task", logger, selected, max_steps=3,
        private=False, high_risk=False, messages=[{"role": "user", "content": "task"}],
        checkpoint_context={"retrieved": selected})
    assert status == "needs_human"
    assert "fresh context" in summary
    assert runtime.router.chat.call_count == runtime.tools.execute.call_count == 1
    assert logger.data["cost"]["tool_calls"] == 1
    assert len(logger.data["activation_revalidations"]) == 2
    assert logger.data["activation_revalidations"][-1]["checks"][0]["status"] == "abstained"
    assert runtime._write_checkpoint.call_args.kwargs["context"]["retrieved"] == selected
