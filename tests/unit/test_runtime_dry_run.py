import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger


@pytest.mark.parametrize("action", [
    {"type": "browser_click", "selector": "button"},
    {"type": "browser_fill", "selector": "input", "value": "example"},
    {"type": "browser_download", "url": "https://example.com/report.csv"},
    {"type": "edit_file", "path": "file.txt", "content": "example"},
    {"type": "create_xlsx", "path": "data.xlsx", "workbook": {"sheets": [{"name": "Data", "rows": [[1]]}]}},
    {"type": "run_command", "command": "python task.py"},
])
@pytest.mark.parametrize("dry_run", [False, True])
def test_action_loop_dry_run_prevents_mutating_tool_dispatch(tmp_path, action, dry_run):
    # Exercise the actual action loop without starting models, SQLite, or browsers.
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    runtime.primary_executor_id = "primary"
    runtime.action_schemas = ActionSchemaRegistry()
    runtime.router = SimpleNamespace(chat=Mock(return_value={"content": json.dumps(action), "usage": {}}))
    runtime.tools = SimpleNamespace(execute=Mock(return_value={"status": "success", "output": "ok", "data": {}}))
    runtime._trace = Mock()
    runtime._emit_progress = Mock()
    runtime._write_checkpoint = Mock()
    runtime._refresh_snapshot_after_action = Mock()
    runtime._compress_messages_if_needed = lambda messages, *args, **kwargs: messages
    logger = TrajectoryLogger("public task", {})
    status, _ = runtime._run_action_loop("public task", logger, [], max_steps=1,
        private=False, high_risk=False, dry_run=dry_run, messages=[{"role": "user", "content": "task"}])
    assert status == "needs_human"
    assert runtime.tools.execute.call_count == (0 if dry_run else 1)
    assert logger.data["actions"][0]["status"] == ("blocked" if dry_run else "success")
    assert runtime._write_checkpoint.called
