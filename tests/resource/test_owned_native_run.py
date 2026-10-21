import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("harbor")
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
from praxile.eval.owned_native_run import run_owned_native

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_preflight_failure_persists_report_and_releases_lease(tmp_path, monkeypatch):
    from praxile.adapters import harbor_native
    image = "python@sha256:" + "a" * 64
    task = BenchmarkTask(task_id="failure-fixture", benchmark="fixture", dataset_revision="1",
        split="development", instruction="public instruction",
        environment=EnvironmentSpec("harbor", "fixture", image),
        evaluator=EvaluatorReference("private", "1", "PRIVATE_REFERENCE", "sha256:" + "f" * 64))
    def reject(**kwargs):
        raise AssertionError("worker preflight must fail before adapter construction")
    monkeypatch.setattr(harbor_native, "PraxileAgent", reject)
    with pytest.raises(ValueError, match="regular wheel"):
        asyncio.run(run_owned_native(task, state_root=tmp_path, image=image,
            wheel=tmp_path / "unused.whl", wheel_sha256="a" * 64,
            model="fixture", base_url="https://provider.invalid/v1"))
    reports = list(tmp_path.glob("harbor-lease-*/execution.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "failed" and report["error_type"] == "ValueError"
    assert report["cleanup_verified"] is True
    assert "PRIVATE_REFERENCE" not in reports[0].read_text()


@pytest.mark.slow
@pytest.mark.skipif(not (os.environ.get("PRAXILE_TEST_LEASE_IMAGE") and os.environ.get("PRAXILE_TEST_WHEEL")),
                    reason="requires pinned Docker image and explicit worker wheel")
def test_owned_runner_executes_native_and_collects_output(tmp_path, monkeypatch):
    from praxile.eval.credential_broker import ChatCredentialBroker
    image = os.environ["PRAXILE_TEST_LEASE_IMAGE"]
    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    source = tmp_path / "source"
    source.mkdir()
    (source / "original.txt").write_bytes(b"public input")
    (source / "private-rubric.txt").write_text("PRIVATE_EVALUATOR_SENTINEL")
    key = tmp_path / "key"
    key.write_text("fixture-host-only-key")
    key.chmod(0o600)
    task = BenchmarkTask(task_id="owned-fixture", benchmark="fixture", dataset_revision="1",
        split="development", instruction="Read inputs/task.txt and create answer.txt.",
        environment=EnvironmentSpec("harbor", "fixture-image", image),
        evaluator=EvaluatorReference("fixture", "1", "PRIVATE_EVALUATOR_SENTINEL", "sha256:" + "f" * 64),
        inputs=(InputArtifact("task.txt", "original.txt", "sha256:" + hashlib.sha256(b"public input").hexdigest()),),
        outputs=(OutputContract("answer.txt", "text/plain"),))
    requests = []
    actions = [
        {"type": "read_file", "path": "inputs/task.txt"},
        {"type": "edit_file", "path": "answer.txt", "content": "fixture complete"},
        {"type": "finish", "summary": "fixture completed", "status": "completed"},
    ]
    def forward(broker, body):
        payload = json.loads(body)
        serialized = json.dumps(payload)
        assert "PRIVATE_EVALUATOR_SENTINEL" not in serialized
        assert "original.txt" not in serialized
        action = actions[min(len(requests), len(actions) - 1)]
        requests.append(payload)
        return {"choices": [{"message": {"content": json.dumps(action)}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    run_options = dict(state_root=tmp_path / "runs", image=image,
        wheel=wheel, wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        model="fixture", base_url="https://provider.invalid/v1", input_source_root=source,
        credential_file=key, max_steps=6, timeout_seconds=90, run_id="owned-fixture")
    report_path = asyncio.run(run_owned_native(task, **run_options))
    report = json.loads(report_path.read_text())
    assert report["status"] == "executor_returned"
    assert report["cleanup_verified"] is True
    assert report["required_outputs_collected"] is True
    assert report["official_evaluation"] == "not_run"
    assert report["promotion_eligible"] is False
    usage = json.loads((report_path.parent / "agent/praxile-broker-usage.json").read_text())
    assert usage["revoked"] is True
    assert (report_path.parent / "outputs/answer.txt").read_text() == "fixture complete"
    result = report_path.parent / "agent/praxile-result.json"
    assert report["source_result_sha256"] == hashlib.sha256(result.read_bytes()).hexdigest()
    actions = json.loads(result.read_text())["trajectory"]["actions"]
    assert any(a["action_type"] == "read_file" and a["observation"]["status"] == "success" for a in actions)
    assert any(a["action_type"] == "edit_file" and a["observation"]["status"] == "success" for a in actions)
    assert len(requests) >= 3
    assert any("inputs/task.txt" in json.dumps(body) for body in requests)
    count = len(requests)
    assert asyncio.run(run_owned_native(task, **run_options, resume=True)) == report_path
    assert len(requests) == count
    with pytest.raises(FileExistsError):
        asyncio.run(run_owned_native(task, **run_options))
    (report_path.parent / "outputs/answer.txt").write_text("corrupted")
    with pytest.raises(ValueError, match="evidence changed"):
        asyncio.run(run_owned_native(task, **run_options, resume=True))
    assert len(requests) == count
