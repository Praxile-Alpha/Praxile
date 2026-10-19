"""Full Harbor Job/Trial pair smoke test; fixture model and fixture verifier."""
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("harbor")
from praxile.eval.credential_broker import ChatCredentialBroker
from praxile.eval.native_pair import encoded, prepare_pair
from praxile.eval.native_pair_run import run_pair
from praxile.eval.native_input_snapshot import snapshot_inputs
from praxile.eval.native_trial_binding import bind_native_trace
from praxile.eval.native_pair_results import collect_pair, task_checksum
from praxile.eval.v2.candidate import ContextCandidate

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.slow]


@pytest.mark.timeout(300)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_WHEEL") or version("harbor") not in {"0.18.0", "0.22.0"},
                   reason="explicit wheel, supported Harbor and Docker required")
@pytest.mark.parametrize("active", [True, False])
def test_real_harbor_job_pair_runs_native_workers(tmp_path, monkeypatch, active, official=False, deliver_correct=True):
    workbuddy = version("harbor") == "0.18.0"
    identities = []
    if workbuddy:
        pytest.importorskip("workbuddy_bench.runner.docker_environment")
        from praxile.adapters.harbor_native import WorkBuddyPraxileAgent
        original_setup = WorkBuddyPraxileAgent.setup
        async def setup_with_identity_check(agent, environment):
            await original_setup(agent, environment)
            observed = await environment.exec("id -u", timeout_sec=10)
            assert observed.return_code == 0 and int(observed.stdout.strip()) != 0
            identities.append(environment.default_user)
        monkeypatch.setattr(WorkBuddyPraxileAgent, "setup", setup_with_identity_check)
    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    task = tmp_path / "task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "instruction.md").write_text("Summarize the available project.")
    (task / "task.toml").write_text(
        '[agent]\ntimeout_sec=120\n' + ('user="dev"\n' if workbuddy else '') + '[verifier]\ntimeout_sec=30\n'
        '[environment]\nbuild_timeout_sec=120\nworkdir="/workspace"\n')
    (task / "environment/Dockerfile").write_text(
        "FROM python:3.12-slim\nRUN mkdir -p /workspace\nWORKDIR /workspace\n")
    (task / "tests/test.sh").write_text("#!/bin/bash\nmkdir -p /logs/verifier\necho 1 > /logs/verifier/reward.txt\n")
    if official:
        for name in tuple(os.environ):
            if name.startswith("WORKBUDDY_VERIFIER_LLM_"):
                monkeypatch.delenv(name)
        source = Path(os.environ["PRAXILE_TEST_WORKBUDDY_DATASET"]).resolve()
        shutil.copytree(source / "shared", tmp_path / "shared")
        shutil.copyfile(source / "dataset.toml", tmp_path / "dataset.toml")
        config_text = (task / "task.toml").read_text()
        (task / "task.toml").write_text('[task]\nname="workbuddy/task"\n'
            '[metadata]\nsource_case="fixture-file-delivery"\n' + config_text)
        (task / "tests/verifier.toml").write_text('schema_version="workbuddy.office.verifier.v1"\n'
            '[run]\ncwd="/workspace"\ncommand="python /tests/check.py"\n')
        (task / "tests/check.py").write_text('''from pathlib import Path
import sys
import xml.etree.ElementTree as ET
output = Path('/workspace/output/report.txt')
passed = output.is_file() and output.read_text() == 'fixture delivered'
logs = Path('/logs/verifier')
logs.mkdir(parents=True, exist_ok=True)
suite = ET.Element('testsuite', tests='1', failures=str(int(not passed)), errors='0', skipped='0')
ET.SubElement(suite, 'testcase', name='delivered_content')
ET.ElementTree(suite).write(logs / 'results.xml')
(logs / 'test_output.txt').write_text('1 passed' if passed else '1 failed')
sys.exit(0 if passed else 1)
''')
    key = tmp_path / "fixture-key"
    key.write_text("not-a-real-credential")
    key.chmod(0o600)
    observed, brokers = [], []
    def forward(broker, body):
        observed.append(b"PAIR_CONTEXT_SENTINEL" in body)
        brokers.append(broker)
        action = {"type": "finish", "summary": "fixture complete", "status": "completed"}
        if official and brokers.count(broker) == 1:
            action = {"type": "edit_file", "path": "output/report.txt",
                      "content": "fixture delivered" if deliver_correct else "incorrect fixture content"}
        return {"choices": [{"message": {"content": json.dumps(action)}}],
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
    if workbuddy:
        job["agents"][0]["import_path"] = "praxile.adapters.harbor_native:WorkBuddyPraxileAgent"
        job["environment"]["import_path"] = "workbuddy_bench.runner.docker_environment:WorkBuddyDockerEnvironment"
    if official:
        job["verifier"] = {"import_path": "workbuddy_bench.judge:CompositeVerifier"}
        job["agents"][0]["kwargs"]["output_directory"] = "output"
        job["artifacts"] = [{"source": "/workspace/output", "exclude": ["__pycache__", "*.pyc"]}]
    root = tmp_path / "pair"
    root.mkdir()
    plan, arms = prepare_pair(job, candidate, repository="fixture/repo" if active else "other/repo", output_dir=root)
    plan.update(schema_version="praxile.native_pair_plan.v2", local_inputs=snapshot_inputs(job))
    for name, value in [("manifest.json", plan), ("candidate-asset.json", candidate),
                        *[(arm + ".json", config) for arm, config in arms.items()]]:
        (root / name).write_bytes(encoded(value))
    state = asyncio.run(run_pair(root))
    assert state["status"] == "jobs_returned"
    assert state["local_inputs_frozen"] is True
    assert len(state["input_checks"]) == 4
    assert all(check["status"] == "unchanged" and check["scope"] == "plan_and_local_inputs"
               for check in state["input_checks"])
    assert observed == ([False, False, active, active] if official else [False, active])
    assert brokers and all(broker.status()["revoked"] for broker in brokers)
    if workbuddy:
        assert identities == ["dev", "dev"]
    for arm in ("baseline", "candidate"):
        trials = list((tmp_path / "jobs" / ("native-pair-" + arm)).glob("*/result.json"))
        assert len(trials) == 1
        trial = json.loads(trials[0].read_text())
        assert task_checksum(task) == trial["task_checksum"]
        assert trial["exception_info"] is None
        assert trial["verifier_result"]["rewards"]["reward"] == (0.0 if official and not deliver_correct else 1.0)
        if official:
            artifact = trials[0].parent / "artifacts/workspace/output/report.txt"
            assert artifact.read_text() == ("fixture delivered" if deliver_correct else "incorrect fixture content")
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
    if official:
        report = collect_pair(root)
        assert report["comparison"]["sample_count"] == 1
        assert len(report["bindings"]) == 2
        assert all(outcome["status"] == "completed" for outcome in report["official_outcomes"])
        assert all(outcome["scores"][0]["value"] == float(deliver_correct) for outcome in report["official_outcomes"])
        assert not report["promotion_eligible"]


@pytest.mark.timeout(300)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_WHEEL") or not os.environ.get("PRAXILE_TEST_WORKBUDDY_DATASET")
                   or version("harbor") != "0.18.0", reason="explicit wheels, upstream dataset, Harbor 0.18 and Docker required")
@pytest.mark.parametrize("deliver_correct", [True, False])
def test_official_composite_verifier_pair_collection(tmp_path, monkeypatch, deliver_correct):
    test_real_harbor_job_pair_runs_native_workers(tmp_path, monkeypatch, True, official=True,
                                                 deliver_correct=deliver_correct)
