import json
import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")]


def command(status, program, timeout=5):
    return [sys.executable, "-m", "praxile.eval.process_supervisor", "--timeout", str(timeout),
            "--status", str(status), "--", sys.executable, "-c", program]


@pytest.mark.parametrize("exit_code", [0, 7])
def test_exit_status_is_preserved(tmp_path, exit_code):
    status = tmp_path / "status.json"
    result = subprocess.run(command(status, f"raise SystemExit({exit_code})"), timeout=10,
                            capture_output=True)
    assert result.returncode == exit_code
    report = json.loads(status.read_text())
    assert report["status"] == ("completed" if exit_code == 0 else "failed")
    assert report["returncode"] == exit_code


def test_deadline_reaps_group_and_closes_inherited_pipes(tmp_path):
    status = tmp_path / "status.json"
    program = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('ready',flush=True); time.sleep(60)"
    result = subprocess.run(command(status, program, .2), timeout=10, capture_output=True)
    assert result.returncode == 124
    assert b"ready" in result.stdout
    assert json.loads(status.read_text())["status"] == "timed_out"


def test_termination_reaps_worker_and_reports_cancellation(tmp_path):
    status = tmp_path / "status.json"
    process = subprocess.Popen(command(status, "import time; time.sleep(60)", 30),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if status.exists() and json.loads(status.read_text())["status"] == "running":
                break
            time.sleep(.01)
        else:
            pytest.fail("supervisor did not start")
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=5)
        assert process.returncode == 143
        assert json.loads(status.read_text())["status"] == "cancelled"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
