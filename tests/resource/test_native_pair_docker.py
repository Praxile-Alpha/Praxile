"""Full Harbor Job/Trial pair smoke test; fixture model and fixture verifier."""
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("harbor")
from praxile.eval.credential_broker import ChatCredentialBroker
from praxile.eval.native_pair import encoded, prepare_pair
from praxile.eval.native_pair_run import run_pair
from praxile.eval.native_trial_binding import bind_native_trace
from praxile.eval.v2.candidate import ContextCandidate

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.slow]


@pytest.mark.timeout(300)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_WHEEL") or version("harbor") != "0.22.0",
                   reason="explicit wheel, Harbor 0.22 and Docker required")
@pytest.mark.parametrize("active", [True, False])
def test_real_harbor_job_pair_runs_native_workers(tmp_path, monkeypatch, active):
    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Summarize the available project.")
    (task / "task.toml").write_text(
        '[agent]\ntimeout_sec=120\n[verifier]\ntimeout_sec=30\n'
        '[environment]\nbuild_timeout_sec=120\nworkdir="/workspace"\n')
    (task / "environment/Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN mkdir -p /workspace\nWORKDIR /workspace\n")
    (task / "tests/test.sh").write_text("#!/bin/bash\nmkdir -p /logs/verifier\necho 1 > /logs/verifier/reward.txt\n")
    key = tmp_path / "fixture-key"
    key.write_text("not-a-real-credential")
    key.chmod(0o600)
    observed, brokers = [], []
    def forward(broker, body):
        observed.append(b"PAIR_CONTEXT_SENTINEL" in body)
        brokers.append(broker)
        return {"choices": [{"message": {"content": json.dumps(
            {"type": "finish", "summary": "fixture complete", "status": "completed"})}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    candidate = ContextCandidate(candidate_id="pair", version="1", title="Transport check",
        candidate_type="context_policy", context_item={"content": "PAIR_CONTEXT_SENTINEL: inspect available files."},
        confidence=0, evidence_refs=("event:fixture",), expected_effect={"hypothesis": "transport only"},
        applies_to={"repositories": ["fixture/repo"], "task_signals": ["available project"],
                    "does_not_apply_when": ["unrelated task"]}).to_dict()
    job = {"job_name": "native-pair", "jobs_dir": str(tmp_path / "jobs"),
        "tasks": [{"path": str(task)}], "datasets": [],
        "environment": {"type": "docker", "delete": True, "force_build": False},
        "agents": [{"import_path": "praxile.adapters.harbor_native:PraxileAgent", "model_name": "fixture",
            "kwargs": {"wheel_path": str(wheel), "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "workspace": "/workspace", "base_url": "https://fixture.invalid/v1",
                "credential_file": str(key), "max_steps": 2, "timeout_seconds": 90}}]}
    root = tmp_path / "pair"
    root.mkdir()
    plan, arms = prepare_pair(job, candidate, repository="fixture/repo" if active else "other/repo", output_dir=root)
    for name, value in [("manifest.json", plan), ("candidate-asset.json", candidate),
                        *[(arm + ".json", config) for arm, config in arms.items()]]:
        (root / name).write_bytes(encoded(value))
    state = asyncio.run(run_pair(root))
    assert state["status"] == "jobs_returned"
    assert observed == [False, active]
    assert brokers and all(broker.status()["revoked"] for broker in brokers)
    for arm in ("baseline", "candidate"):
        trials = list((tmp_path / "jobs" / ("native-pair-" + arm)).glob("*/result.json"))
        assert len(trials) == 1
        trial = json.loads(trials[0].read_text())
        assert trial["exception_info"] is None
        assert trial["verifier_result"]["rewards"] == {"reward": 1.0}
        evidence = {}
        def read(relative):
            raw = (trials[0].parent / relative).read_bytes()
            evidence[relative] = "sha256:" + hashlib.sha256(raw).hexdigest()
            return json.loads(raw)
        binding = bind_native_trace(trial, read, evidence)
        assert binding["status"] == "bound" and not binding["provenance_attested"]
        assert binding["experience_mode"] == ("fresh_disabled" if arm == "baseline" else "development_candidate")
        native = read("agent/praxile-result.json")
        assert native["trajectory"]["result"]["status"] == "completed"
        if arm == "candidate":
            assert native["development_context"]["activation"]["activated"] is active
            assert native["development_context"]["injected"] is active
