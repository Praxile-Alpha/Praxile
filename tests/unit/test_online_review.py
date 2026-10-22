import asyncio
import hashlib
import json

import pytest

from praxile.eval import online_stream as stream_module
from praxile.eval.online_review import audit_online_review, prepare_online_review, run_online_review
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
    tasks = [task]
    if getattr(request, "param", False) in ("multi", "budget"):
        tasks.append(BenchmarkTask.from_dict({**task.to_dict(), "task_id": "online-second"}))
        judge["artifacts"]["online-second"] = dict(judge["artifacts"][task.task_id])
    stream = stream_module.OnlineStream.create(tmp_path / "stream", tasks, repository="docs",
        chat_budget={"max_calls": 3, "output_budget": 1000, "ttl_seconds": 600, "request_byte_budget": 1000000}
                    if getattr(request, "param", False) == "budget" else None,
        judge=judge if getattr(request, "param", False) else None,
        input_source_roots={item.task_id: tmp_path for item in tasks}, execution={"wheel": tmp_path / "worker.whl",
            "wheel_sha256": "a" * 64, "model": "executor", "base_url": "https://provider.invalid/v1", "credential_file": key})
    async def run(task, **options):
        if options.get("shared_chat_budget"):
            options["shared_chat_budget"].reserve("executor", 10, request_bytes=10)
        root = options["state_root"] / "lease"
        for name in ("inputs", "outputs", "agent"):
            (root / name).mkdir(parents=True)
        (root / "inputs/source.txt").write_bytes(source)
        (root / "outputs/result.txt").write_bytes(source)
        worker = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
            "trajectory": {"task_id": "internal", "start_time": "2026-10-22T00:00:00Z", "actions": []}}
        identity = None
        if options.get("development_only"):
            from praxile.eval.native_context import owned_candidate_identity, plan_candidate
            value = json.loads(options["development_candidate_file"].read_text())
            identity = owned_candidate_identity(task, options["development_candidate_file"],
                options["development_candidate_sha256"], options["repository"], development_only=True)
            context = plan_candidate(value, task.instruction, options["repository"], development_only=True)
            content = context.pop("content")
            context["injected"] = bool(content)
            worker.update(experience_mode="development_candidate", development_context=context)
            if content:
                worker["trajectory"]["experimental_context"] = {"content": content, "sha256": sha(content.encode()), "injected": True}
        trace = json.dumps(worker).encode()
        (root / "agent/praxile-result.json").write_bytes(trace)
        report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
            "task_fingerprint": task.fingerprint, "image": task.environment.revision,
            "status": "executor_returned", "cleanup_verified": True, "required_outputs_collected": True,
            "wheel_sha256": options["wheel_sha256"], "model": options["model"],
            "endpoint_sha256": sha(options["base_url"].encode()), "max_steps": options.get("max_steps", 50),
            "timeout_seconds": options.get("timeout_seconds", 900), "model_request_options": options.get("model_request_options", {}),
            "task_local_memory_enabled": options.get("task_local_memory_enabled", False),
            "public_inputs": [{"path": "inputs/source.txt", "content_digest": task.inputs[0].content_digest}],
            "source_result_sha256": sha(trace), "outputs": [{"path": "result.txt", "required": True,
                "status": "collected", "sha256": sha(source), "bytes": len(source)}]}
        if identity:
            report["development_context_identity"] = identity
        path = root / "execution.json"
        path.write_text(json.dumps(report))
        return path
    monkeypatch.setattr(stream_module, "run_owned_native", run)
    for _ in tasks:
        asyncio.run(stream.run_next())
    return stream, judge


@pytest.fixture
def composed(completed, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from praxile.eval import online_probe, online_composition
    from praxile.eval.v2.candidate import ContextCandidate
    stream, judge = completed
    scope = {"repositories": ["docs"], "task_signals": ["checklist"], "does_not_apply_when": ["unrelated"]}
    parents = [ContextCandidate(candidate_id=name, version="1", title=name, candidate_type="context_policy",
        context_item={"content": "Inspect before revising"}, confidence=0, evidence_refs=("event:source",),
        source_task_ids=("online-task",), expected_effect={"hypothesis": "bounded"}, applies_to=scope).to_dict()
        for name in ("first", "second")]
    monkeypatch.setattr(online_probe, "run_owned_native", stream_module.run_owned_native)
    probe = tmp_path / "probe"
    asyncio.run(online_probe.run_online_probe(stream.root, [0], parents, directory=probe))
    class Router:
        def chat(self, messages, **kwargs):
            return {"content": json.dumps({"decision": "compose", "content": "Verify source before revising",
                "hypothesis": "bounded", "evidence_refs": ["event:source"]}), "usage": {}, "finish_reason": "stop"}
    @contextmanager
    def router(*args, **kwargs):
        yield Router(), {"model": "fixture"}
    monkeypatch.setattr(online_composition, "configured_design_router", router)
    root = tmp_path / "composition"
    asyncio.run(online_composition.run_online_composition(SimpleNamespace(), stream.root, probe_directory=probe,
        manifest_sha256=sha((probe / "manifest.json").read_bytes()), report_sha256=sha((probe / "report.json").read_bytes()),
        directory=root, composition_id="composed", applies_to=scope))
    return stream, judge, {"probe_directory": probe, "composition_directory": root,
        "manifest_sha256": sha((root / "manifest.json").read_bytes()), "report_sha256": sha((root / "report.json").read_bytes()),
        "directory": tmp_path / "paired-review"}


@pytest.mark.parametrize("completed", [False, True], indirect=True)
def test_paired_composition_review_is_isolated(composed, monkeypatch, tmp_path, capsys):
    from praxile.eval.online_composition_review import prepare_composition_reviews, run_composition_reviews
    stream, judge, options = composed
    before = (stream.root / "stream.json").read_bytes()
    composition_before = (options["composition_directory"] / "report.json").read_bytes()
    calls = []
    def forward(self, raw):
        calls.append(raw)
        body = json.loads(raw)
        units = json.loads(body["messages"][1]["content"])["output_units"]
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": [
                {"id": "r", "verdict": "preserved", "output_refs": [units[0]["id"]], "reason": "fixture"}]})}}]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    prepare_composition_reviews(stream.root, 0, judge=judge, **options)
    from praxile.eval.online_stream_cli import main
    judge_path = tmp_path / "judge-preview.json"
    judge_path.write_text(json.dumps({**judge, "credential_file": "MISSING_SECRET", "evaluator_root": str(judge["evaluator_root"])}))
    assert main(["review-composition", "--stream", str(stream.root), "--task-index", "0",
        "--probe-directory", str(options["probe_directory"]), "--composition-directory", str(options["composition_directory"]),
        "--manifest-sha256", options["manifest_sha256"], "--report-sha256", options["report_sha256"],
        "--output", str(options["directory"]), "--judge-config", str(judge_path)]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "composition_review_preview_only"
    assert not calls and not options["directory"].exists()
    report_path = run_composition_reviews(stream.root, 0, judge=judge, **options)
    report = json.loads(report_path.read_text())
    assert [row["arm"] for row in report["reviews"]] == ["baseline", "composition"]
    assert report["decision"] == "KEEP_BASE" and report["learning_feedback_allowed"] is False
    assert len(calls) == 2
    for row in report["reviews"]:
        path = report_path.parent / row["receipt"]
        assert sha(path.read_bytes()) == row["receipt_sha256"]
        assert json.loads(path.read_text())["verdict_counts"]["preserved"] == 1
        assert json.loads(path.read_text())["judge_precommitted_before_execution"] == stream.status()["judge_precommitted_before_execution"]
    assert (stream.root / "stream.json").read_bytes() == before
    assert (options["composition_directory"] / "report.json").read_bytes() == composition_before
    with pytest.raises(ValueError):
        run_composition_reviews(stream.root, 0, judge=judge, **options)
    assert len(calls) == 2


def test_composition_pair_preflights_both_outputs_before_judge(composed, monkeypatch):
    from praxile.eval.online_composition_review import run_composition_reviews
    stream, judge, options = composed
    path = options["composition_directory"] / "rerun/task-0/composition/lease/outputs/result.txt"
    path.write_text("tampered")
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("model accessed"))
    with pytest.raises(ValueError):
        run_composition_reviews(stream.root, 0, judge=judge, **options)
    assert not options["directory"].exists()


@pytest.mark.parametrize("completed", ["budget"], indirect=True)
def test_replaced_stream_quota_is_not_accepted(completed):
    from praxile.eval.persistent_chat_budget import PersistentChatBudget
    stream, _ = completed
    (stream.root / "quota.sqlite").unlink()
    PersistentChatBudget.create(stream.root / "quota.sqlite", max_calls=100, output_budget=100000,
                                ttl_seconds=600)
    with pytest.raises(ValueError, match="frozen limits"):
        stream.shared_budget()


@pytest.mark.parametrize("completed", ["budget"], indirect=True)
def test_shared_stream_quota_survives_reopen_and_caps_judge(completed, tmp_path, monkeypatch):
    stream, judge = completed
    assert stream.status()["chat_budget"]["remaining_calls"] == 1
    calls = []
    def fail(*args):
        calls.append(1)
        raise TimeoutError("fixture transport")
    monkeypatch.setattr(ChatCredentialBroker, "_forward", fail)
    for index, task_id in enumerate(stream.status()["completed_task_ids"]):
        reopened = stream_module.OnlineStream(stream.root)
        settings = {**judge, "artifacts": {task_id: judge["artifacts"][task_id]}}
        path = run_online_review(reopened.root, index, judge=settings, directory=tmp_path / f"review-{index}")
        receipt = json.loads(path.read_text())
        assert receipt["shared_stream_budget"] is True
        assert receipt["status"] == "abstained"
        assert not (path.parent / "quota.sqlite").exists()
    assert calls == [1]
    budget = stream.status()["chat_budget"]
    assert budget["remaining_calls"] == 0
    assert budget["roles"]["executor"]["reserved_calls"] == 2
    assert budget["roles"]["judge"]["reserved_calls"] == 1
    assert budget["remaining_request_bytes"] < 1000000 - 20
    (stream.root / "quota.sqlite").unlink()
    with pytest.raises(ValueError):
        stream.status()
    assert not (stream.root / "quota.sqlite").exists()


@pytest.mark.parametrize("completed", ["multi"], indirect=True)
def test_multiple_real_receipts_summary_cli(completed, tmp_path, monkeypatch, capsys):
    from praxile.eval.online_stream_cli import main
    stream, judge = completed
    def forward(self, raw):
        body = json.loads(raw)
        data = json.loads(body["messages"][1]["content"])
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": [
                {"id": "r", "verdict": "preserved", "output_refs": [data["output_units"][0]["id"]], "reason": "Fixture"}]})}}]}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    entries = []
    for index, task_id in enumerate(stream.status()["completed_task_ids"]):
        settings = {**judge, "artifacts": {task_id: judge["artifacts"][task_id]}}
        directory = tmp_path / f"review-{index}"
        receipt = run_online_review(stream.root, index, judge=settings, directory=directory)
        entries.append({"task_index": index, "directory": str(directory), "receipt_sha256": sha(receipt.read_bytes()),
                        "judge": {**settings, "credential_file": "MISSING_SECRET", "evaluator_root": str(judge["evaluator_root"])}})
    manifest = tmp_path / "reviews.json"
    manifest.write_text(json.dumps({"reviews": entries}))
    monkeypatch.setattr(ChatCredentialBroker, "_forward", lambda *args: pytest.fail("summary invoked model"))
    assert main(["summary", "--stream", str(stream.root), "--reviews", str(manifest)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["planned_tasks"] == result["tasks_with_complete_labels"] == 2
    assert result["planned_task_mean_bounds"] == {"lower": 1, "upper": 1}
    assert result["judge_precommitted_before_execution"] is True


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
    audit = audit_online_review(stream.root, 0, judge=judge, directory=root, receipt_sha256=sha(path.read_bytes()))
    assert audit["status"] == ("unknown" if verdict == "uncertain" else "complete_judge_labels")
    assert audit["local_file_consistency_checked"] is True
    assert audit["semantic_accuracy_verified"] is False
    from praxile.eval.online_summary import summarize_online_stream
    summary = summarize_online_stream(stream.root, [{"task_index": 0, "directory": root,
        "receipt_sha256": sha(path.read_bytes()), "judge": judge}])
    assert summary["tasks_with_complete_labels"] == (0 if verdict == "uncertain" else 1)
    assert summary["observed_task_mean"] == (None if verdict == "uncertain" else 1)
    original = path.read_bytes()
    for field, value in (("task_index", 1), ("promotion_eligible", True), ("verdict_counts", {})):
        changed = {**receipt, field: value}
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            audit_online_review(stream.root, 0, judge=judge, directory=root, receipt_sha256=sha(path.read_bytes()))
        path.write_bytes(original)
    manifest_path = root / "judge/manifest.json"
    manifest_raw = manifest_path.read_bytes()
    manifest_path.write_text(json.dumps({**json.loads(manifest_raw), "model": "other-judge"}))
    with pytest.raises(ValueError, match="evidence changed"):
        audit_online_review(stream.root, 0, judge=judge, directory=root, receipt_sha256=sha(original))
    receipt["evidence_sha256"]["manifest.json"] = sha(manifest_path.read_bytes())
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="judge mismatch"):
        audit_online_review(stream.root, 0, judge=judge, directory=root, receipt_sha256=sha(path.read_bytes()))
    manifest_path.write_bytes(manifest_raw)
    path.write_bytes(original)
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


def test_failed_review_is_preserved_and_cannot_replay(completed, tmp_path, monkeypatch, capsys):
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
    audit = audit_online_review(stream.root, 0, judge=judge, directory=root,
                                receipt_sha256=sha((root / "receipt.json").read_bytes()))
    assert audit["status"] == "unknown" and audit["verdict_counts"] is None
    from praxile.eval.online_stream_cli import main
    config = tmp_path / "judge.json"
    config.write_text(json.dumps({**judge, "credential_file": "MISSING_SECRET",
                                 "evaluator_root": str(judge["evaluator_root"])}))
    assert main(["audit-review", "--stream", str(stream.root), "--task-index", "0",
        "--judge-config", str(config), "--output", str(root), "--receipt-sha256",
        sha((root / "receipt.json").read_bytes())]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unknown"
    assert stream.status() == before
    with pytest.raises(ValueError):
        run_online_review(stream.root, 0, judge=judge, directory=root)
    assert calls == [1]
