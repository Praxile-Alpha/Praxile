"""Workspace execution exclusion, including owner process death."""
import os
import signal
import subprocess
import sys
import selectors

import pytest

from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.utils import file_lock

pytestmark = [pytest.mark.resource, pytest.mark.runtime_resource, pytest.mark.sqlite_resource]


def test_conflict_precedes_initialization_and_does_not_close_active_tools(tmp_path, monkeypatch):
    runtime = AgentRuntime(Config.load(tmp_path))
    def forbidden(*args, **kwargs):
        pytest.fail("contender cannot initialize state or close active tools")
    monkeypatch.setattr(runtime.store, "initialize", forbidden)
    monkeypatch.setattr(runtime.tools, "close", forbidden)
    with file_lock(runtime.config.paths.state / "runtime-execution", timeout_seconds=0):
        for resume in (None, "task"):
            with pytest.raises(TimeoutError, match="runtime-execution"):
                runtime.run("task", resume=resume)


@pytest.mark.parametrize("outcome", ["success", "body_failure", "cleanup_failure"])
def test_lock_spans_cleanup_and_releases_after_every_exit(tmp_path, monkeypatch, outcome):
    runtime = AgentRuntime(Config.load(tmp_path))
    path = runtime.config.paths.state / "runtime-execution"
    def assert_owned():
        with pytest.raises(TimeoutError):
            with file_lock(path, timeout_seconds=0):
                pytest.fail("lock released too early")
    def resume(*args, **kwargs):
        assert_owned()
        if outcome == "body_failure":
            raise ValueError("fixture failure")
        return {"status": "completed"}
    def close():
        assert_owned()
        if outcome == "cleanup_failure":
            raise ValueError("fixture failure")
    monkeypatch.setattr(runtime, "_resume_run", resume)
    monkeypatch.setattr(runtime.tools, "close", close)
    if outcome == "success":
        assert runtime.run("task", resume="task")["status"] == "completed"
    else:
        with pytest.raises(ValueError, match="fixture failure"):
            runtime.run("task", resume="task")
    with file_lock(path, timeout_seconds=0):
        pass


@pytest.mark.integration
@pytest.mark.skipif(os.name != "posix", reason="requires POSIX SIGKILL")
def test_process_owner_excludes_same_workspace_until_sigkill(tmp_path, monkeypatch):
    code = '''
import sys, time
from pathlib import Path
from praxile.config import Config
from praxile.runtime import AgentRuntime
runtime = AgentRuntime(Config.load(Path(sys.argv[1])))
def resume(*args, **kwargs):
    print("LOCKED", flush=True)
    time.sleep(60)
runtime._resume_run = resume
runtime.run("task", resume="task")
'''
    child = subprocess.Popen([sys.executable, "-B", "-c", code, str(tmp_path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    runtime = AgentRuntime(Config.load(tmp_path))
    monkeypatch.setattr(runtime, "_resume_run", lambda *a, **k: {"status": "completed"})
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(10), "runtime owner did not signal readiness"
            assert child.stdout.readline().strip() == b"LOCKED"
        with pytest.raises(TimeoutError):
            runtime.run("task", resume="task")
        other = AgentRuntime(Config.load(tmp_path / "other"))
        monkeypatch.setattr(other, "_resume_run", lambda *a, **k: {"status": "completed"})
        assert other.run("task", resume="task")["status"] == "completed"
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=10)
        assert child.returncode == -signal.SIGKILL
        assert runtime.run("task", resume="task")["status"] == "completed"
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.communicate(timeout=10)
        runtime.tools.close()
