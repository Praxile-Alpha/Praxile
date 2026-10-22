import asyncio
import hashlib
import json

import pytest

from praxile.eval import online_stream as stream_module
from praxile.eval.online_review import prepare_online_review, run_online_review
from praxile.eval.credential_broker import ChatCredentialBroker
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def completed(tmp_path, monkeypatch, request):
    source = b"retain source"
    evaluator = tmp_path / "evaluator"
    evaluator.mkdir()
    requirements = json.dumps({"requirements": [{"id": "r", "requirement": "retain source", "source_quote": "source"}]}).encode()
    (evaluator / "requirements.json").write_bytes(requirements)
    task = BenchmarkTask(task_id="online-task", benchmark="fixture", dataset_revision="1", split="online_stream",
        instruction="checklist", environment=EnvironmentSpec("harbor", "fixture", "image@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("praxile-content-review", "1", "requirements.json", "sha256:" + sha(requirements)),
        inputs=(InputArtifact("source.txt", "source.txt", "sha256:" + sha(source)),),
        outputs=(OutputContract("result.txt", "text/plain"),))
    key = tmp_path / "key"
    key.write_text("fixture-secret")
    key.chmod(0o600)
    judge = {"credential_file": key, "endpoint": "https://judge.invalid/v1/chat/completions", "model": "judge",
        "public": True, "total_output_tokens": 100, "evaluator_root": evaluator,
        "artifacts": {task.task_id: {"source_path": "source.txt", "output_path": "result.txt"}}}
    stream = stream_module.OnlineStream.create(tmp_path / "stream", [task], repository="docs",
        judge=judge if getattr(request, "param", False) else None,
        input_source_roots={task.task_id: tmp_path}, execution={"wheel": tmp_path / "worker.whl",
            "wheel_sha256": "a" * 64, "model": "executor", "base_url": "https://provider.invalid/v1", "credential_file": key})
    async def run(task, **options):
        root = options["state_root"] / "lease"
        for name in ("inputs", "outputs", "agent"):
            (root / name).mkdir(parents=True)
        (root / "inputs/source.txt").write_bytes(source)
        (root / "outputs/result.txt").write_bytes(source)
        trace = json.dumps({"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
            "trajectory": {"task_id": "internal", "start_time": "2026-10-22T00:00:00Z", "actions": []}}).encode()
        (root / "agent/praxile-result.json").write_bytes(trace)
        report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
            "task_fingerprint": task.fingerprint, "image": task.environment.revision,
            "status": "executor_returned", "cleanup_verified": True, "required_outputs_collected": True,
            "public_inputs": [{"path": "inputs/source.txt", "content_digest": task.inputs[0].content_digest}],
            "source_result_sha256": sha(trace), "outputs": [{"path": "result.txt", "required": True,
                "status": "collected", "sha256": sha(source), "bytes": len(source)}]}
        path = root / "execution.json"
        path.write_text(json.dumps(report))
        return path
    monkeypatch.setattr(stream_module, "run_owned_native", run)
    asyncio.run(stream.run_next())
    return stream, judge


@pytest.mark.parametrize("verdict", ["preserved", "uncertain"])
@pytest.mark.parametrize("completed", [False, True], indirect=True)
def test_review_binds_outputs_without_changing_learning_state(completed, tmp_path, monkeypatch, verdict):
    from praxile.eval.online_design import collect_online_evidence
    stream, judge = completed
    before = (stream.root / "stream.json").read_bytes()
    learning_before = collect_online_evidence(stream.root)
    calls = []
    def forward(self, raw):
        calls.append(raw)
        body = json.loads(raw)
        data = json.loads(body["messages"][1]["content"])
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": [
                {"id": "r", "verdict": verdict, "output_refs": [data["output_units"][0]["id"]], "reason": "Fixture"}]})}}]}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    root = tmp_path / "review"
    prepared = prepare_online_review(stream.root, 0, judge=judge, directory=root)
    assert not root.exists() and not calls
    path = run_online_review(stream.root, 0, judge=judge, directory=root)
    receipt = json.loads(path.read_text())
    assert receipt["track"] == "online_stream"
    assert receipt["status"] == "reviewed"
    assert receipt["verdict_counts"][verdict] == 1
    assert receipt["execution_binding"] == prepared["binding"]
    assert receipt["judge_precommitted_before_execution"] == stream.status()["judge_precommitted_before_execution"]
    assert receipt["learning_feedback_allowed"] is False
    for name, digest in receipt["evidence_sha256"].items():
        assert sha((root / "judge" / name).read_bytes()) == digest
    assert (stream.root / "stream.json").read_bytes() == before
    assert collect_online_evidence(stream.root) == learning_before
    assert len(calls) == 1
    with pytest.raises(ValueError):
        run_online_review(stream.root, 0, judge=judge, directory=root)
    assert len(calls) == 1


@pytest.mark.parametrize("completed", [True], indirect=True)
@pytest.mark.parametrize("field,value", [("model", "different"), ("total_output_tokens", 99),
    ("batch_size", 2), ("endpoint", "https://other.invalid/v1/chat/completions"),
    ("timeout_per_call", 30), ("request_options", {"reasoning_effort": "low"})])
def test_precommitted_judge_cannot_change(completed, tmp_path, monkeypatch, field, value):
    stream, judge = completed
    judge[field] = value
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("model accessed"))
    with pytest.raises(ValueError, match="precommitted"):
        run_online_review(stream.root, 0, judge=judge, directory=tmp_path / "review")
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("defect", ["future", "inside", "no_consent", "changed_output", "invalid_endpoint"])
def test_invalid_review_rejected_before_model(completed, tmp_path, monkeypatch, defect):
    stream, judge = completed
    root, index = tmp_path / "review", 0
    if defect == "future":
        index = 1
    elif defect == "inside":
        root = stream.root / "review"
    elif defect == "no_consent":
        judge["public"] = False
    elif defect == "invalid_endpoint":
        judge["endpoint"] = {"url": "invalid"}
    else:
        (stream.root / "tasks/0/lease/outputs/result.txt").write_text("tampered")
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("model accessed"))
    with pytest.raises(ValueError):
        run_online_review(stream.root, index, judge=judge, directory=root)
    assert not root.exists()


def test_cli_review_preview_does_not_load_credentials(completed, tmp_path, monkeypatch, capsys):
    from praxile.eval.online_stream_cli import main
    stream, judge = completed
    judge = {**judge, "credential_file": "MISSING_SECRET", "evaluator_root": str(judge["evaluator_root"])}
    config = tmp_path / "judge.json"
    config.write_text(json.dumps(judge))
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("model accessed"))
    assert main(["review", "--stream", str(stream.root), "--task-index", "0", "--judge-config", str(config),
                 "--output", str(tmp_path / "review")]) == 0
    result = capsys.readouterr().out
    assert "review_preview_only" in result and "MISSING_SECRET" not in result
    assert not (tmp_path / "review").exists()


@pytest.mark.parametrize("defect", ["evaluator_inside", "evaluator_symlink_inside", "output_symlink", "existing_output"])
def test_review_storage_boundaries_fail_before_model(completed, tmp_path, monkeypatch, defect):
    stream, judge = completed
    root = tmp_path / "review"
    if defect.startswith("evaluator"):
        private = stream.root / "private"
        private.mkdir()
        original = judge["evaluator_root"] / "requirements.json"
        (private / "requirements.json").write_bytes(original.read_bytes())
        if defect == "evaluator_inside":
            judge["evaluator_root"] = private
        else:
            original.unlink()
            original.symlink_to(private / "requirements.json")
    elif defect == "output_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(stream.root, target_is_directory=True)
        root = alias / "review"
    else:
        root.mkdir()
    before = (stream.root / "stream.json").read_bytes()
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("model accessed"))
    with pytest.raises(ValueError):
        run_online_review(stream.root, 0, judge=judge, directory=root)
    assert (stream.root / "stream.json").read_bytes() == before
    assert not (root / "receipt.json").exists()


def test_failed_review_is_preserved_and_cannot_replay(completed, tmp_path, monkeypatch):
    stream, judge = completed
    before = stream.status()
    calls = []
    def fail(*args):
        calls.append(1)
        raise TimeoutError("fixture timeout")
    monkeypatch.setattr(ChatCredentialBroker, "_forward", fail)
    root = tmp_path / "review"
    run_online_review(stream.root, 0, judge=judge, directory=root)
    receipt = json.loads((root / "receipt.json").read_text())
    assert receipt["status"] == "abstained"
    assert receipt["verdict_counts"] is None
    usage = json.loads((root / "judge/broker-usage.json").read_text())["usage"]
    assert usage["failed_calls"] == 1 and usage["total_tokens"]["input"] is None
    assert receipt["automatic_replay_allowed"] is False
    assert stream.status() == before
    with pytest.raises(ValueError):
        run_online_review(stream.root, 0, judge=judge, directory=root)
    assert calls == [1]
