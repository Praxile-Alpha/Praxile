"""Fault injection at the action/checkpoint boundary; no external model calls."""
import json
import os
import signal
import subprocess
import sys

import pytest

from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger

pytestmark = [pytest.mark.resource, pytest.mark.runtime_resource, pytest.mark.sqlite_resource]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = Config.load(tmp_path)
    config.data["checkpoint"]["every_steps"] = 100
    instance = AgentRuntime(config)
    instance.store.initialize(config)
    monkeypatch.setattr(instance, "_refresh_snapshot_after_action", lambda *args: None)
    monkeypatch.setattr(instance.router, "chat", lambda *args, **kwargs: {
        "content": json.dumps({"type": "edit_file", "path": "effect.txt", "content": "once"})})
    yield instance
    instance.tools.close()
    for provider in instance.router.providers.values():
        close = getattr(getattr(provider, "transport", None), "close", None)
        if close:
            close()


def run_step(runtime, logger):
    return runtime._run_action_loop("Write effect.txt", logger, [], max_steps=1,
        private=False, high_risk=False, messages=[{"role": "user", "content": "Write effect.txt"}],
        checkpoint_context={"task": "Write effect.txt"})


@pytest.mark.parametrize("failure_point", ["tool", "completion_checkpoint"])
def test_unknown_side_effect_blocks_resume_before_model(runtime, tmp_path, monkeypatch, failure_point):
    logger = TrajectoryLogger("Write effect.txt", {})
    write = runtime.store.write_checkpoint
    def fail_commit(checkpoint):
        if failure_point == "completion_checkpoint" and "pending_action" not in checkpoint["trajectory"]:
            raise OSError("fixture disk failure")
        return write(checkpoint)
    monkeypatch.setattr(runtime.store, "write_checkpoint", fail_commit)
    def execute(*args, **kwargs):
        checkpoint = runtime.store.load_checkpoint(logger.task_id)
        assert checkpoint["trajectory"]["pending_action"]["status"] == "outcome_unconfirmed"
        (tmp_path / "effect.txt").write_text("once")
        if failure_point == "tool":
            raise KeyboardInterrupt()
        return {"status": "success", "output": "written", "data": {}}
    monkeypatch.setattr(runtime.tools, "execute", execute)
    with pytest.raises(KeyboardInterrupt if failure_point == "tool" else OSError):
        run_step(runtime, logger)
    checkpoint = runtime.store.load_checkpoint(logger.task_id)
    assert checkpoint["trajectory"]["pending_action"]["step"] == 1
    assert (tmp_path / "effect.txt").read_text() == "once"
    def forbidden(*args, **kwargs):
        pytest.fail("unconfirmed action must not trigger a model request")
    monkeypatch.setattr(runtime.router, "chat", forbidden)
    with pytest.raises(ValueError, match="unconfirmed action"):
        runtime._resume_run(logger.task_id, task_override=None, test_commands=None,
                            max_steps=1, dry_run=False)


def test_intent_write_failure_prevents_dispatch(runtime, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("fixture disk failure")
    monkeypatch.setattr(runtime.store, "write_checkpoint", fail)
    def forbidden(*args, **kwargs):
        pytest.fail("must persist intent before tool dispatch")
    monkeypatch.setattr(runtime.tools, "execute", forbidden)
    with pytest.raises(OSError):
        run_step(runtime, TrajectoryLogger("Write effect.txt", {}))


def test_completed_observation_is_saved_despite_sampling(runtime, monkeypatch):
    monkeypatch.setattr(runtime.tools, "execute", lambda *args, **kwargs: {
        "status": "success", "output": "written", "data": {}})
    logger = TrajectoryLogger("Write effect.txt", {})
    run_step(runtime, logger)
    checkpoint = runtime.store.load_checkpoint(logger.task_id)
    assert "pending_action" not in checkpoint["trajectory"]
    assert checkpoint["trajectory"]["actions"][-1]["observation"]["output"] == "written"
    assert checkpoint["messages"][-1]["content"].startswith("Observation:")


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="requires POSIX SIGKILL")
def test_sigkill_after_side_effect_rejects_fresh_process_resume(tmp_path, monkeypatch):
    code = '''
import json, os, signal, sys
from pathlib import Path
from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger
root = Path(sys.argv[1])
runtime = AgentRuntime(Config.load(root))
runtime.store.initialize(runtime.config)
runtime.router.chat = lambda *a, **k: {"content": json.dumps(
    {"type": "edit_file", "path": "effect.txt", "content": "once"})}
def execute(*args, **kwargs):
    (root / "effect.txt").write_text("once")
    os.kill(os.getpid(), signal.SIGKILL)
runtime.tools.execute = execute
runtime._run_action_loop("Write effect.txt", TrajectoryLogger("Write effect.txt", {}), [],
    max_steps=1, private=False, high_risk=False,
    messages=[{"role": "user", "content": "Write effect.txt"}])
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)],
                            capture_output=True, timeout=15)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    assert (tmp_path / "effect.txt").read_text() == "once"
    checkpoint, = (tmp_path / ".praxile/checkpoints").glob("*.json")
    task_id = json.loads(checkpoint.read_text())["task_id"]
    runtime = AgentRuntime(Config.load(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("SIGKILL recovery must not call the model")
    monkeypatch.setattr(runtime.router, "chat", forbidden)
    try:
        with pytest.raises(ValueError, match="unconfirmed action"):
            runtime.run(resume=task_id, task="Write effect.txt")
    finally:
        runtime.tools.close()
