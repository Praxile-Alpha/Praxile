import asyncio
import hashlib
import json

import pytest

from praxile.eval import online_stream as module
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference
from praxile.eval.native_context import owned_candidate_identity
from praxile.eval.v2.candidate import ContextCandidate


def create(tmp_path):
    tasks = [BenchmarkTask(task_id=name, benchmark="fixture", dataset_revision="1", split="online_stream",
        instruction="Make a checklist", environment=EnvironmentSpec("harbor", "fixture", "python@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("judge", "1", "PRIVATE", "sha256:" + "b" * 64)) for name in ("first", "second")]
    stream = module.OnlineStream.create(tmp_path / "stream", tasks, repository="docs", execution={
        "wheel": tmp_path / "worker.whl", "wheel_sha256": "a" * 64, "model": "fixture",
        "base_url": "https://provider.invalid/v1", "credential_file": tmp_path / "key"})
    return stream, tasks


def candidate(source="first"):
    return ContextCandidate(candidate_id="online", version="1", title="Fixture", candidate_type="context_policy",
        context_item={"content": "Inspect before revising."}, confidence=0,
        evidence_refs=("event:source",), source_task_ids=(source,), expected_effect={"hypothesis": "fixture"},
        applies_to={"repositories": ["docs"], "task_signals": ["checklist"],
                    "does_not_apply_when": ["unrelated"]}).to_dict()


def fake_runner(calls):
    async def run(task, **options):
        calls.append((task.task_id, options))
        if "online_protocol" in options:
            identity = owned_candidate_identity(task, options["development_candidate_file"],
                options["development_candidate_sha256"], options["repository"], online_protocol=options["online_protocol"])
            assert identity["track"] == "online_stream"
        root = options["state_root"] / "lease"
        (root / "agent").mkdir(parents=True)
        raw = b'{"fixture":true}'
        (root / "agent/praxile-result.json").write_bytes(raw)
        report = root / "execution.json"
        report.write_text(json.dumps({"task_fingerprint": task.fingerprint, "status": "executor_returned",
            "cleanup_verified": True, "source_result_sha256": hashlib.sha256(raw).hexdigest()}))
        return report
    return run


def test_ordered_stream_approved_update_reaches_next_task_and_survives_reopen(tmp_path, monkeypatch):
    stream, _ = create(tmp_path)
    calls = []
    monkeypatch.setattr(module, "run_owned_native", fake_runner(calls))
    with pytest.raises(ValueError):
        stream.accept_candidate(candidate(), approved=True, approved_by="human")
    first = asyncio.run(stream.run_next())
    assert first["completed_task_ids"] == ["first"]
    with pytest.raises(ValueError):
        stream.accept_candidate(candidate())
    with pytest.raises(ValueError, match="prefix"):
        stream.accept_candidate(candidate("second"), approved=True, approved_by="human")
    stream.accept_candidate(candidate(), approved=True, approved_by="human")
    reopened = module.OnlineStream(stream.root)
    last = asyncio.run(reopened.run_next())
    assert last["completed_task_ids"] == ["first", "second"]
    assert last["track"] == "online_stream" and last["promotion_eligible"] is False
    assert "online_protocol" not in calls[0][1]
    assert calls[1][1]["online_protocol"]["completed_task_ids"] == ["first"]
    with pytest.raises(ValueError, match="replay"):
        asyncio.run(reopened.run_next())
    state = json.loads((stream.root / "stream.json").read_text())
    assert [event["kind"] for event in state["events"]] == ["dispatch", "completed", "approved_candidate", "dispatch", "completed"]


@pytest.mark.parametrize("cancelled", [False, True])
def test_uncertain_dispatch_is_not_retried_or_updated(tmp_path, monkeypatch, cancelled):
    stream, _ = create(tmp_path)
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise asyncio.CancelledError() if cancelled else TimeoutError("fixture")
    monkeypatch.setattr(module, "run_owned_native", fail)
    with pytest.raises(asyncio.CancelledError if cancelled else TimeoutError):
        asyncio.run(stream.run_next())
    stream = module.OnlineStream(stream.root)
    assert stream.status()["pending_index"] == 0
    with pytest.raises(ValueError):
        asyncio.run(stream.run_next())
    with pytest.raises(ValueError):
        stream.accept_candidate(candidate(), approved=True, approved_by="human")
    assert calls == [1]


def test_changed_evidence_blocks_later_task(tmp_path, monkeypatch):
    stream, _ = create(tmp_path)
    calls = []
    monkeypatch.setattr(module, "run_owned_native", fake_runner(calls))
    asyncio.run(stream.run_next())
    (stream.root / "tasks/0/lease/agent/praxile-result.json").write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        asyncio.run(stream.run_next())
    assert len(calls) == 1


def test_competing_dispatch_is_rejected_while_first_owns_step(tmp_path, monkeypatch):
    stream, _ = create(tmp_path)
    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        run = fake_runner([])
        async def wait(task, **options):
            started.set()
            await release.wait()
            return await run(task, **options)
        monkeypatch.setattr(module, "run_owned_native", wait)
        owner = asyncio.create_task(stream.run_next())
        await started.wait()
        try:
            with pytest.raises(ValueError, match="replay"):
                await module.OnlineStream(stream.root).run_next()
        finally:
            release.set()
            await owner
    asyncio.run(exercise())


def test_failed_dispatch_persistence_never_calls_executor(tmp_path, monkeypatch):
    stream, _ = create(tmp_path)
    calls = []
    monkeypatch.setattr(module, "run_owned_native", fake_runner(calls))
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(module, "_persist", fail)
    with pytest.raises(OSError):
        asyncio.run(stream.run_next())
    assert not calls
    assert stream.status()["pending_index"] is None


def test_online_protocol_cannot_be_relabelled_heldout_or_use_future_sources(tmp_path):
    from dataclasses import replace
    from praxile.eval.online_native_protocol import validate_online_protocol
    _, tasks = create(tmp_path)
    protocol = {"completed_task_ids": ["first"], "task_id": "second", "candidate_file_sha256": "a" * 64,
                "repository": "docs"}
    assert validate_online_protocol(protocol, tasks[1], candidate(), "a" * 64, "docs")
    with pytest.raises(ValueError):
        validate_online_protocol(protocol, replace(tasks[1], split="heldout"), candidate(), "a" * 64, "docs")
    with pytest.raises(ValueError):
        validate_online_protocol(protocol, tasks[1], candidate("second"), "a" * 64, "docs")


def test_cli_create_preview_execute_approve_and_reopen(tmp_path, monkeypatch, capsys):
    import zipfile
    from praxile.eval.online_stream_cli import main
    _, tasks = create(tmp_path)
    wheel = tmp_path / "worker.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("praxile/eval/native_worker.py", "# fixture")
        archive.writestr("praxile/eval/process_supervisor.py", "# fixture")
    options = {"wheel": str(wheel), "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "model": "fixture", "base_url": "https://provider.invalid/v1", "credential_file": str(tmp_path / "UNREAD_KEY")}
    config = tmp_path / "execution.json"
    config.write_text(json.dumps(options))
    root = tmp_path / "cli-stream"
    arguments = ["create", "--stream", str(root), "--execution-config", str(config), "--repository", "docs"]
    for task in tasks:
        path = tmp_path / (task.task_id + ".json")
        path.write_text(json.dumps(task.to_dict()))
        arguments += ["--task", str(path)]
    calls = []
    monkeypatch.setattr(module, "run_owned_native", fake_runner(calls))
    assert main(arguments) == 0
    assert not calls
    assert "UNREAD_KEY" not in capsys.readouterr().out
    before = (root / "stream.json").read_bytes()
    assert main(["next", "--stream", str(root)]) == 0
    assert main(["status", "--stream", str(root)]) == 0
    assert (root / "stream.json").read_bytes() == before
    assert not calls
    assert main(["next", "--stream", str(root), "--execute"]) == 0
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(candidate()))
    approval = ["accept", "--stream", str(root), "--candidate", str(path),
                "--candidate-sha256", hashlib.sha256(path.read_bytes()).hexdigest()]
    before = (root / "stream.json").read_bytes()
    assert main(approval) == 0
    assert (root / "stream.json").read_bytes() == before
    assert main(approval + ["--approve"]) == 2
    assert (root / "stream.json").read_bytes() == before
    assert main(approval + ["--approve", "--approved-by", "reviewer"]) == 0
    assert main(["next", "--stream", str(root), "--execute"]) == 0
    assert [task for task, _ in calls] == ["first", "second"]
    output = capsys.readouterr()
    assert "Inspect before revising" not in output.out + output.err
    assert "PRIVATE" not in output.out + output.err
    assert "UNREAD_KEY" not in output.out + output.err
    assert main(arguments) == 2


def test_cli_changed_candidate_digest_does_not_modify_ledger(tmp_path, monkeypatch):
    from praxile.eval.online_stream_cli import main
    stream, _ = create(tmp_path)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([]))
    asyncio.run(stream.run_next())
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(candidate()))
    before = (stream.root / "stream.json").read_bytes()
    assert main(["accept", "--stream", str(stream.root), "--candidate", str(path),
        "--candidate-sha256", "0" * 64, "--approve", "--approved-by", "human"]) == 2
    assert (stream.root / "stream.json").read_bytes() == before


@pytest.mark.parametrize("field,value", [("base_url", "https://user:secret@provider.invalid/v1"),
    ("timeout_seconds", 99999), ("max_steps", True), ("task_local_memory_enabled", "yes"),
    ("model_request_options", {"messages": []})])
def test_invalid_execution_settings_rejected_before_creation(tmp_path, field, value):
    stream, tasks = create(tmp_path)
    options = json.loads((stream.root / "stream.json").read_text())["spec"]["execution"]
    options[field] = value
    root = tmp_path / "invalid"
    with pytest.raises(ValueError):
        module.OnlineStream.create(root, tasks, execution=options, repository="docs")
    assert not root.exists()
