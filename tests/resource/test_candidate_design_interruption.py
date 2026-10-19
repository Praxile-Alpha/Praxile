"""A hard-killed designer must leave intent and must not replay on resume."""
import json
import os
import subprocess
import sys
import textwrap

import pytest


pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX supervision")]


def test_killed_design_retains_intent_and_resume_refuses_request(tmp_path):
    script = tmp_path / "design.py"
    script.write_text(textwrap.dedent('''
        from contextlib import contextmanager
        from pathlib import Path
        from types import SimpleNamespace
        import sys
        import time
        from praxile.eval.v2 import candidate_design_run as module

        state = Path(sys.argv[1])
        @contextmanager
        def router(*args, **kwargs):
            yield object(), {"model": "fixture", "config_digest": "fixed"}
        def generate(**kwargs):
            with (state / "calls.txt").open("a") as handle:
                handle.write("request\\n")
            time.sleep(60)
            raise AssertionError("supervisor failed to stop generation")
        module.configured_design_router = router
        module.collect_development_evidence = lambda *a, **k: {"evidence": [{"fixture": True}]}
        module.design_development_candidates = generate
        module.run_candidate_design(SimpleNamespace(paths=SimpleNamespace(state=state)), None,
            design_id="interrupted", experiment_id="dev", arm="baseline",
            development_task_ids=["dev"], heldout_task_ids=["heldout"], applies_to={},
            resume=len(sys.argv) > 2)
    '''), encoding="utf-8")
    status = tmp_path / "supervision.json"
    worker = [sys.executable, str(script), str(tmp_path)]
    process = subprocess.Popen([sys.executable, "-m", "praxile.eval.process_supervisor",
        "--timeout", "2", "--status", str(status), "--", *worker],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _, stderr = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
    assert process.returncode == 124, stderr.decode()
    root = tmp_path / "eval/v2/candidate_designs/interrupted"
    intent = json.loads((root / "request-intent.json").read_text())
    assert intent["status"] == "outcome_unknown"
    assert intent["automatic_replay_allowed"] is False
    trajectory = json.loads((root / "trajectory.json").read_text())
    assert trajectory["candidate_design_request_intent"] == intent
    assert not (root / "generated.json").exists()
    assert json.loads(status.read_text())["worker_reaped"] is True
    retry = subprocess.run([*worker, "resume"], capture_output=True, timeout=10)
    assert retry.returncode != 0
    assert b"Resume cannot resend a model request" in retry.stderr
    assert (tmp_path / "calls.txt").read_text() == "request\n"
