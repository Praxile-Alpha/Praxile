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


@pytest.mark.parametrize("invalid", ["missing_credential", "resume"])
def test_shared_budget_preflight_rejects_bypass_and_resume(tmp_path, invalid):
    from praxile.eval.credential_broker import SharedChatBudget
    budget = SharedChatBudget(max_calls=2, output_budget=100, ttl_seconds=10)
    with pytest.raises(ValueError, match="credential file|cannot be resumed"):
        asyncio.run(run_owned_native(None, state_root=tmp_path, image="unused",
            wheel=tmp_path / "unused.whl", wheel_sha256="a" * 64, model="fixture", base_url="unused",
            shared_chat_budget=budget, credential_file=tmp_path / "key" if invalid == "resume" else None,
            resume=invalid == "resume"))
    assert list(tmp_path.iterdir()) == []
    assert budget.status()["remaining_calls"] == 2


@pytest.mark.parametrize("missing_receipt", [False, True])
@pytest.mark.parametrize("memory_enabled", [False, True])
@pytest.mark.parametrize("with_candidate", [False, True])
def test_owned_controller_forwards_shared_budget_and_records_boundaries(tmp_path, monkeypatch, missing_receipt, memory_enabled, with_candidate):
    from types import SimpleNamespace
    import praxile.eval.owned_native_run as module
    from praxile.adapters import harbor_native
    from praxile.eval.credential_broker import SharedChatBudget
    from praxile.utils import write_json
    budget = SharedChatBudget(max_calls=2, output_budget=100, ttl_seconds=10)
    seen = []
    candidate_kwargs = {}
    if with_candidate:
        from praxile.eval.v2.candidate import ContextCandidate
        candidate = ContextCandidate(candidate_id="dev", version="1", title="Fixture",
            candidate_type="context_policy", context_item={"content": "Inspect before finishing."},
            confidence=0, evidence_refs=("event:fixture",), expected_effect={"hypothesis": "fixture"},
            applies_to={"repositories": ["public/docs"], "task_signals": ["checklist"],
                        "does_not_apply_when": ["unrelated task"]})
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(candidate.to_dict()))
        candidate_kwargs = {"development_candidate_file": path,
            "development_candidate_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "development_only": True, "repository": "public/docs"}
    lease_root = tmp_path / ("harbor-lease-" + "a" * 32)
    class Lease:
        def __init__(self, *args, **kwargs):
            self.root = lease_root
            self.root.mkdir()
            self.public_inputs = []
            self.workspace_path = None
        async def prepare(self):
            pass
        async def run_adapter(self, agent, instruction, context):
            assert seen[-1]["shared_chat_budget"] is budget
            assert seen[-1]["task_local_memory_enabled"] is memory_enabled
            if with_candidate:
                assert seen[-1]["development_candidate_file"] == str(candidate_kwargs["development_candidate_file"])
                assert seen[-1]["development_candidate_sha256"] == candidate_kwargs["development_candidate_sha256"]
                assert seen[-1]["development_only"] is True
                assert seen[-1]["repository"] == "public/docs"
            else:
                assert seen[-1]["development_candidate_file"] is None
            budget.reserve("executor", 10)
            write_json(self.root / "agent/praxile-result.json", {"fixture": True})
            if not missing_receipt:
                write_json(self.root / "agent/praxile-broker-usage.json", {"shared_budget": budget.status()})
        async def cleanup(self):
            pass
    def agent(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(worker_environment_sha256="a" * 64)
    monkeypatch.setattr(module, "HarborDockerLease", Lease)
    monkeypatch.setattr(harbor_native, "PraxileAgent", agent)
    monkeypatch.setattr("praxile.eval.worker_preflight.check_worker_wheel", lambda *args: None)
    image = "python@sha256:" + "a" * 64
    task = BenchmarkTask(task_id="fixture", benchmark="fixture", dataset_revision="1",
        split="development", instruction="public", environment=EnvironmentSpec("harbor", "fixture", image),
        evaluator=EvaluatorReference("private", "1", "private", "sha256:" + "f" * 64))
    call = run_owned_native(task, state_root=tmp_path, image=image, wheel=tmp_path / "fixture.whl",
        wheel_sha256="a" * 64, model="fixture", base_url="https://provider.invalid/v1",
        credential_file=tmp_path / "key", shared_chat_budget=budget, run_id="shared-fixture",
        task_local_memory_enabled=memory_enabled, **candidate_kwargs)
    if missing_receipt:
        with pytest.raises(ValueError, match="broker usage receipt"):
            asyncio.run(call)
    else:
        asyncio.run(call)
    report = json.loads((lease_root / "execution.json").read_text())
    assert report["task_local_memory_enabled"] is memory_enabled
    if with_candidate:
        loaded_candidate = ContextCandidate.from_dict(json.loads(path.read_text()))
        assert report["development_context_identity"]["candidate_digest"] == loaded_candidate.digest
        assert "Inspect before finishing" not in json.dumps(report)
    else:
        assert "development_context_identity" not in report
    assert report["shared_chat_budget_start"]["remaining_calls"] == 2
    assert report["shared_chat_budget_end"]["remaining_calls"] == 1
    assert report["cleanup_verified"] is True
    assert report["status"] == ("failed" if missing_receipt else "executor_returned")
    if not missing_receipt:
        receipt = lease_root / "agent/praxile-broker-usage.json"
        assert report["broker_usage_sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
        claim = json.loads((tmp_path / "run-claims/shared-fixture.json").read_text())
        assert claim["status"] == "completed"
        assert claim["files"][receipt.relative_to(tmp_path).as_posix()] == report["broker_usage_sha256"]


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


@pytest.mark.parametrize("with_capability", [False, True])
@pytest.mark.slow
@pytest.mark.skipif(not (os.environ.get("PRAXILE_TEST_LEASE_IMAGE") and os.environ.get("PRAXILE_TEST_WHEEL")),
                    reason="requires pinned Docker image and explicit worker wheel")
def test_owned_runner_executes_native_and_collects_output(tmp_path, monkeypatch, with_capability):
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
        credential_file=key, max_steps=6, timeout_seconds=90, run_id="owned-fixture", persist_workspace=True)
    if with_capability:
        if not os.environ.get("PRAXILE_TEST_XLSX_WHEELS"):
            pytest.skip("explicit dependency wheels required")
        run_options.update(required_capabilities=["xlsx_values.v1"], dependency_wheels=[
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(Path(os.environ["PRAXILE_TEST_XLSX_WHEELS"]).glob("*.whl"))])
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
    assert report["persist_workspace"] is True
    assert (report_path.parent / report["workspace"] / "answer.txt").read_text() == "fixture complete"
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
    if with_capability:
        capabilities = report_path.parent / "agent/praxile-capabilities.json"
        assert json.loads(capabilities.read_text())["reports"][0]["status"] == "passed"
        with pytest.raises(ValueError, match="frozen execution plan"):
            asyncio.run(run_owned_native(task, **dict(run_options, required_capabilities=[]), resume=True))
        original = capabilities.read_bytes()
        capabilities.write_text("{}")
        with pytest.raises(ValueError, match="evidence changed"):
            asyncio.run(run_owned_native(task, **run_options, resume=True))
        capabilities.write_bytes(original)
    with pytest.raises(ValueError, match="frozen execution plan"):
        asyncio.run(run_owned_native(task, **dict(run_options, persist_workspace=False), resume=True))
    with pytest.raises(FileExistsError):
        asyncio.run(run_owned_native(task, **run_options))
    (report_path.parent / "outputs/answer.txt").write_text("corrupted")
    with pytest.raises(ValueError, match="evidence changed"):
        asyncio.run(run_owned_native(task, **run_options, resume=True))
    assert len(requests) == count
