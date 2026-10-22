import asyncio
import hashlib
import json

import pytest

from praxile.eval import online_stream as module
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference
from praxile.eval.native_context import owned_candidate_identity
from praxile.eval.v2.candidate import ContextCandidate


def create(tmp_path, *, chat_budget=None):
    tasks = [BenchmarkTask(task_id=name, benchmark="fixture", dataset_revision="1", split="online_stream",
        instruction="Make a checklist", environment=EnvironmentSpec("harbor", "fixture", "python@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("judge", "1", "PRIVATE", "sha256:" + "b" * 64)) for name in ("first", "second")]
    stream = module.OnlineStream.create(tmp_path / "stream", tasks, repository="docs", chat_budget=chat_budget, execution={
        "wheel": tmp_path / "worker.whl", "wheel_sha256": "a" * 64, "model": "fixture",
        "base_url": "https://provider.invalid/v1", "credential_file": tmp_path / "key"})
    return stream, tasks


def candidate(source="first"):
    return ContextCandidate(candidate_id="online", version="1", title="Fixture", candidate_type="context_policy",
        context_item={"content": "Inspect before revising."}, confidence=0,
        evidence_refs=("event:source",), source_task_ids=(source,), expected_effect={"hypothesis": "fixture"},
        applies_to={"repositories": ["docs"], "task_signals": ["checklist"],
                    "does_not_apply_when": ["unrelated"]}).to_dict()


def fake_runner(calls, *, native=False, expose=False):
    async def run(task, **options):
        calls.append((task.task_id, options))
        if "online_protocol" in options:
            identity = owned_candidate_identity(task, options["development_candidate_file"],
                options["development_candidate_sha256"], options["repository"], online_protocol=options["online_protocol"])
            assert identity["track"] == "online_stream"
        root = options["state_root"] / "lease"
        (root / "agent").mkdir(parents=True)
        raw = (json.dumps({"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
            "trajectory": {"task_id": "native-" + task.task_id, "start_time": "2026-10-22T00:00:00Z",
                "user_task": "PRIVATE_SENTINEL", "actions": [{"step": 1, "action_type": "run_command",
                    "input": {"command": "PRIVATE_SENTINEL"}, "status": "failure",
                    "observation": {"data": {"exit_code": 1, "stdout": "PRIVATE_SENTINEL"}}}]}}).encode()
            if native else b'{"fixture":true}')
        if native and options.get("development_only"):
            from praxile.eval.native_context import plan_candidate
            value = json.loads(options["development_candidate_file"].read_text())
            context = plan_candidate(value, task.instruction, options["repository"], development_only=True)
            content = context.pop("content")
            context["injected"] = bool(expose and content)
            record = json.loads(raw)
            record["experience_mode"] = "development_candidate"
            record["development_context"] = context
            if context["injected"]:
                record["trajectory"]["experimental_context"] = {"content": content,
                    "sha256": hashlib.sha256(content.encode()).hexdigest(), "injected": True}
            raw = json.dumps(record).encode()
        (root / "agent/praxile-result.json").write_bytes(raw)
        report = root / "execution.json"
        data = {"task_fingerprint": task.fingerprint, "status": "executor_returned",
            "cleanup_verified": True, "source_result_sha256": hashlib.sha256(raw).hexdigest(),
            "image": task.environment.revision, "wheel_sha256": options["wheel_sha256"], "model": options["model"],
            "endpoint_sha256": hashlib.sha256(options["base_url"].encode()).hexdigest(),
            "max_steps": options.get("max_steps", 50), "timeout_seconds": options.get("timeout_seconds", 900),
            "model_request_options": options.get("model_request_options", {}),
            "task_local_memory_enabled": options.get("task_local_memory_enabled", False)}
        if options.get("development_only"):
            data["development_context_identity"] = owned_candidate_identity(task,
                options["development_candidate_file"], options["development_candidate_sha256"],
                options["repository"], development_only=True)
        report.write_text(json.dumps(data))
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


@pytest.mark.parametrize("propose", [False, True])
@pytest.mark.parametrize("budgeted", [False, True])
def test_online_design_uses_bound_prefix_and_never_auto_approves(tmp_path, monkeypatch, propose, budgeted):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from praxile.eval.online_design import collect_online_evidence, run_online_design
    from praxile.eval.v2 import candidate_design_run
    stream, _ = create(tmp_path, chat_budget={"max_calls": 1, "output_budget": 4000, "ttl_seconds": 600} if budgeted else None)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([], native=True))
    asyncio.run(stream.run_next())
    bundle = collect_online_evidence(stream.root)
    assert "PRIVATE_SENTINEL" not in json.dumps(bundle)
    assert bundle["evidence"][0]["task_id"] == "first"
    assert bundle["sources"][0]["native_task_id"] == "native-first"
    calls = []
    quotas = []
    class Router:
        def chat(self, messages, **kwargs):
            if budgeted:
                quotas[-1].reserve("proposal_composer", 4000)
            calls.append(messages)
            assert "PRIVATE_SENTINEL" not in json.dumps(messages)
            body = json.loads(messages[1]["content"])
            assert {item["task_id"] for item in body["evidence"]} == {"first"}
            drafts = [{"content": "Bounded advice " + str(index), "hypothesis": "Hypothesis " + str(index),
                       "evidence_refs": body["allowed_refs"]} for index in range(3)]
            result = {"decision": "propose", "candidates": drafts[:2], "composition": drafts[2]} if propose else {"decision": "abstain"}
            return {"content": json.dumps(result), "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "finish_reason": "stop"}
    @contextmanager
    def router(*args, **kwargs):
        if budgeted:
            quotas.append(kwargs["shared_chat_budget"])
        yield Router(), {"model": "fixture", "config_digest": "fixed"}
    monkeypatch.setattr(candidate_design_run, "configured_design_router", router)
    config = SimpleNamespace(paths=SimpleNamespace(state=tmp_path / "design-state"))
    kwargs = {"design_id": "online-design", "applies_to": candidate()["applies_to"]}
    before = stream.status()
    result = run_online_design(config, stream.root, **kwargs)
    assert result["track"] == "online_stream"
    assert result["source_ledger_sha256"] == before["ledger_sha256"]
    if budgeted:
        assert stream.status()["ledger_sha256"] == before["ledger_sha256"]
        assert stream.status()["chat_budget"]["remaining_calls"] == 0
    else:
        assert stream.status() == before
    assert run_online_design(config, stream.root, **kwargs, resume=True) == result
    assert len(calls) == 1
    root = config.paths.state / "eval/v2/candidate_designs/online-design"
    if budgeted:
        trace = json.loads((root / "trajectory.json").read_text())
        assert trace["shared_chat_budget_start"]["remaining_calls"] == 1
        assert trace["shared_chat_budget_end"]["remaining_calls"] == 0
    assert json.loads((root / "report.json").read_text())["track"] == "online_stream"
    if propose:
        generated = json.loads((root / result["candidate_files"][0]).read_text())
        assert generated["source_task_ids"] == ["first"]
        stream.accept_candidate(generated, approved=True, approved_by="human")
        assert stream.status()["candidate_digest"] is not None
    else:
        assert result["decision"] == "NO_ENTRY"


def test_online_design_cli_preview_does_not_load_model_config(tmp_path, monkeypatch, capsys):
    from praxile.eval.online_stream_cli import main
    from praxile.config import Config
    stream, _ = create(tmp_path)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([], native=True))
    asyncio.run(stream.run_next())
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps(candidate()["applies_to"]))
    monkeypatch.setattr(Config, "load", lambda *args: pytest.fail("preview accessed model configuration"))
    assert main(["design", "--stream", str(stream.root), "--project", str(tmp_path), "--design-id", "preview",
                 "--scope", str(scope)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "design_preview_only" and result["evidence_count"] == 1


@pytest.mark.parametrize("expose", [False, True])
def test_online_prefix_probe_isolated_and_does_not_advance_stream(tmp_path, monkeypatch, capsys, expose):
    from praxile.eval.online_probe import run_online_probe, prepare_online_probe
    stream, _ = create(tmp_path)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([], native=True))
    asyncio.run(stream.run_next())
    parents = [candidate(), {**candidate(), "candidate_id": "other"}]
    for parent in parents:
        parent["expected_effect"]["measurement"] = {"kind": "repeated_exact_operations", "operation": "run_command", "direction": "decrease"}
    from praxile.eval import online_probe
    calls = []
    monkeypatch.setattr(online_probe, "run_owned_native", fake_runner(calls, native=True, expose=expose))
    root = tmp_path / "probe"
    before = (stream.root / "stream.json").read_bytes()
    plan = prepare_online_probe(stream.root, [0], parents, directory=root)
    assert plan["track"] == "online_prefix_development_probe"
    assert not root.exists() and not calls
    from praxile.eval.online_stream_cli import main
    args = ["probe", "--stream", str(stream.root), "--task-index", "0", "--output", str(root)]
    for index, parent in enumerate(parents):
        path = tmp_path / f"parent-{index}.json"
        path.write_text(json.dumps(parent))
        args.extend(["--candidate", str(path), "--candidate-sha256", hashlib.sha256(path.read_bytes()).hexdigest()])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["planned_executions"] == 3
    assert not root.exists() and not calls
    result = asyncio.run(run_online_probe(stream.root, [0], parents, directory=root))
    report = json.loads(result.read_text())
    assert report["status"] == "executions_returned_not_scored"
    assert [row["arm"] for row in report["attempts"]] == ["baseline", "first", "second"]
    assert [row["candidate_exposure_verified"] for row in report["attempts"]] == [False, expose, expose]
    assert len({str(options["state_root"]) for _, options in calls}) == 3
    assert "development_candidate_file" not in calls[0][1]
    assert calls[1][1]["development_only"] is True
    assert calls[2][1]["development_only"] is True
    assert "PRIVATE_SENTINEL" not in json.dumps(report)
    from praxile.eval.online_probe_feedback import audit_online_probe
    kwargs = {"directory": root, "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
              "report_sha256": hashlib.sha256(result.read_bytes()).hexdigest()}
    feedback = audit_online_probe(stream.root, **kwargs)
    assert main(["probe-feedback", "--stream", str(stream.root), "--probe-directory", str(root),
        "--manifest-sha256", kwargs["manifest_sha256"], "--report-sha256", kwargs["report_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out) == feedback
    assert "PRIVATE_SENTINEL" not in json.dumps(feedback)
    assert len(feedback["parent_reports"]) == 2
    for parent in feedback["parent_reports"]:
        episode = parent["improvement_episodes"][0]
        assert episode["verdict"] == "confounded"
        assert episode["behavior_probe"]["status"] == "measured"
        assert episode["behavior_probe"]["delta"] == 0
        assert episode["behavior_probe"]["candidate_exposure_verified"] is expose
    original = result.read_bytes()
    report["attempts"][1]["candidate_exposure_verified"] = not expose
    result.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="digest mismatch"):
        audit_online_probe(stream.root, **kwargs)
    with pytest.raises(ValueError, match="mechanism summary"):
        audit_online_probe(stream.root, **{**kwargs, "report_sha256": hashlib.sha256(result.read_bytes()).hexdigest()})
    result.write_bytes(original)
    execution_path = root / report["attempts"][0]["report"]
    execution_original = execution_path.read_bytes()
    execution_path.write_text(json.dumps({**json.loads(execution_original), "model": "other-model"}))
    report = json.loads(original)
    report["attempts"][0]["report_sha256"] = hashlib.sha256(execution_path.read_bytes()).hexdigest()
    result.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="conditions"):
        audit_online_probe(stream.root, **{**kwargs, "report_sha256": hashlib.sha256(result.read_bytes()).hexdigest()})
    execution_path.write_bytes(execution_original)
    result.write_bytes(original)
    assert (stream.root / "stream.json").read_bytes() == before
    with pytest.raises(ValueError):
        asyncio.run(run_online_probe(stream.root, [0], parents, directory=root))
    assert len(calls) == 3


@pytest.mark.parametrize("defect", ["future", "duplicate_index", "future_source", "duplicate_parent", "inside", "interrupted"])
def test_online_probe_rejects_invalid_scope_or_replay(tmp_path, monkeypatch, defect):
    from praxile.eval import online_probe
    stream, _ = create(tmp_path)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([], native=True))
    asyncio.run(stream.run_next())
    parents = [candidate(), {**candidate(), "candidate_id": "other"}]
    indices, root = [0], tmp_path / "probe"
    if defect == "future":
        indices = [1]
    elif defect == "duplicate_index":
        indices = [0, 0]
    elif defect == "future_source":
        parents[1] = {**parents[1], "source_task_ids": ["second"]}
    elif defect == "duplicate_parent":
        parents[1] = parents[0]
    elif defect == "inside":
        root = stream.root / "probe"
    calls = []
    async def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("fixture interrupted")
    monkeypatch.setattr(online_probe, "run_owned_native", fail)
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(online_probe.run_online_probe(stream.root, indices, parents, directory=root))
    if defect == "interrupted":
        assert json.loads((root / "report.json").read_text())["status"] == "outcome_unknown"
        with pytest.raises(ValueError):
            asyncio.run(online_probe.run_online_probe(stream.root, indices, parents, directory=root))
        assert calls == [1]
    else:
        assert not root.exists() and not calls


@pytest.mark.parametrize("decision", ["compose", "abstain", "timeout"])
def test_online_composition_audits_feedback_and_reruns_without_adopting(tmp_path, monkeypatch, decision):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from praxile.eval import online_composition, online_probe
    stream, _ = create(tmp_path)
    monkeypatch.setattr(module, "run_owned_native", fake_runner([], native=True))
    asyncio.run(stream.run_next())
    before = (stream.root / "stream.json").read_bytes()
    parents = [candidate(), {**candidate(), "candidate_id": "other"}]
    executions, requests = [], []
    monkeypatch.setattr(online_probe, "run_owned_native", fake_runner(executions, native=True, expose=True))
    probe = tmp_path / "probe"
    asyncio.run(online_probe.run_online_probe(stream.root, [0], parents, directory=probe))
    class Router:
        def chat(self, messages, **kwargs):
            requests.append(messages)
            assert "PRIVATE_SENTINEL" not in json.dumps(messages)
            if decision == "timeout":
                raise TimeoutError("fixture")
            payload = json.loads(messages[1]["content"])
            value = {"decision": "abstain"} if decision == "abstain" else {
                "decision": "compose", "content": "Check before revising", "hypothesis": "Bounded combination",
                "evidence_refs": payload["allowed_refs"]}
            return {"content": json.dumps(value), "usage": {"prompt_tokens": 5, "completion_tokens": 5}, "finish_reason": "stop"}
    @contextmanager
    def router(*args, **kwargs):
        yield Router(), {"model": "fixture"}
    monkeypatch.setattr(online_composition, "configured_design_router", router)
    kwargs = {"probe_directory": probe,
        "manifest_sha256": hashlib.sha256((probe / "manifest.json").read_bytes()).hexdigest(),
        "report_sha256": hashlib.sha256((probe / "report.json").read_bytes()).hexdigest(),
        "directory": tmp_path / "composition", "composition_id": "combined", "applies_to": candidate()["applies_to"]}
    if decision == "timeout":
        with pytest.raises(TimeoutError):
            asyncio.run(online_composition.run_online_composition(SimpleNamespace(), stream.root, **kwargs))
    else:
        asyncio.run(online_composition.run_online_composition(SimpleNamespace(), stream.root, **kwargs))
    report = json.loads((kwargs["directory"] / "report.json").read_text())
    assert report["decision"] == "KEEP_BASE" and report["candidate_adopted"] is False
    if decision == "compose":
        assert report["status"] == "composition_rerun_not_scored"
        assert len(executions) == 5
        rerun = json.loads((kwargs["directory"] / "rerun/report.json").read_text())
        assert [row["arm"] for row in rerun["attempts"]] == ["baseline", "composition"]
        assert rerun["attempts"][1]["candidate_exposure_verified"] is True
    else:
        assert report["status"] == ("outcome_unknown" if decision == "timeout" else "composition_abstained")
        assert len(executions) == 3
    with pytest.raises(ValueError):
        asyncio.run(online_composition.run_online_composition(SimpleNamespace(), stream.root, **kwargs))
    assert len(requests) == 1
    assert (stream.root / "stream.json").read_bytes() == before
