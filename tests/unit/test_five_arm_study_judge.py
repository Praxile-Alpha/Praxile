import asyncio
import hashlib
import json

import pytest

from praxile.eval import five_arm_execution as module
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
from praxile.eval.credential_broker import ChatCredentialBroker
from praxile.eval.five_arm_judge import judge_identity
from praxile.eval.five_arm_plan import compile_plan, ARMS, IDENTITIES
from praxile.eval.five_arm_selection import selection_policy
from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.schema import canonical_json


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path):
    source = b"retain source"
    evaluator = tmp_path / "private"
    evaluator.mkdir()
    requirements = json.dumps({"requirements": [{"id": "r", "requirement": "retain source", "source_quote": "source"}]}).encode()
    (evaluator / "requirements.json").write_bytes(requirements)
    task = BenchmarkTask(task_id="task", benchmark="fixture", dataset_revision="1", split="development",
        instruction="checklist", environment=EnvironmentSpec("harbor", "fixture", "image@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("praxile-content-review", "1", "requirements.json", "sha256:" + sha(requirements)),
        inputs=(InputArtifact("source.txt", "source.txt", "sha256:" + sha(source)),),
        outputs=(OutputContract("result.txt", "text/plain"),))
    candidate = ContextCandidate(candidate_id="dev", version="1", title="Fixture", candidate_type="context_policy",
        context_item={"content": "Inspect before finishing."}, confidence=0.0, evidence_refs=("event:fixture",),
        expected_effect={"hypothesis": "fixture"}, applies_to={"repositories": ["docs"], "task_signals": ["checklist"],
        "does_not_apply_when": ["unrelated"]})
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate.to_dict()))
    key = tmp_path / "key"
    key.write_text("fixture-secret")
    key.chmod(0o600)
    policy = selection_policy("first_delivered")
    execution = {"wheel": tmp_path / "unused.whl", "wheel_sha256": "a" * 64, "model": "executor",
        "base_url": "https://provider.invalid/v1", "credential_file": key, "output_budget": 400,
        "development_candidate_file": path, "development_candidate_sha256": sha(path.read_bytes()),
        "repository": "docs", "terminal_selection": policy}
    judge = {"credential_file": key, "endpoint": "https://judge.invalid/v1/chat/completions", "model": "judge",
        "public": True, "total_output_tokens": 100, "evaluator_root": evaluator,
        "artifacts": {"task": {"source_path": "source.txt", "output_path": "result.txt"}}}
    identities = {name: "sha256:" + "a" * 64 for name in IDENTITIES}
    identities["terminal_selection"] = "sha256:" + sha(canonical_json(policy).encode())
    identities["judge"] = "sha256:" + sha(canonical_json(judge_identity(judge, {"task": task})).encode())
    plan = compile_plan({"study_id": "judged", "task_ids": ["task"], "heldout_task_ids": ["heldout"],
        "repetitions": 1, "repeat_attempts": 2, "identities": identities,
        "budget": {"total_tokens": 1000, "model_calls": 4, "wall_seconds": 60}, "arm_order": list(ARMS)})
    return task, plan, execution, judge, source


@pytest.mark.parametrize("verdict", ["preserved", "uncertain", None])
def test_five_cells_review_only_selected_outputs_using_same_quota(tmp_path, monkeypatch, verdict):
    task, plan, execution, judge, source = fixture(tmp_path)
    calls = []
    async def run(task, **options):
        calls.append("execute")
        options["shared_chat_budget"].reserve("executor", 100)
        root = options["state_root"] / "lease"
        for name in ("inputs", "outputs", "agent"):
            (root / name).mkdir(parents=True)
        (root / "inputs/source.txt").write_bytes(source)
        (root / "outputs/result.txt").write_bytes(source)
        trajectory = json.dumps({"schema_version": "praxile.native_benchmark_run.v1"}).encode()
        (root / "agent/praxile-result.json").write_bytes(trajectory)
        broker_receipt = json.dumps({"usage": {"schema_version": "praxile.broker_usage.v1",
            "admitted_calls": 1, "responses": 1, "failed_calls": 0, "inflight_calls": 0,
            "latency_seconds": 0.2, "known_tokens": {"input": 10, "output": 5},
            "reported_calls": {"input": 1, "output": 1}, "total_tokens": {"input": 10, "output": 5}}}).encode()
        (root / "agent/praxile-broker-usage.json").write_bytes(broker_receipt)
        report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
            "model": options["model"], "wheel_sha256": options["wheel_sha256"],
            "endpoint_sha256": sha(options["base_url"].encode()), "max_steps": options["max_steps"],
            "model_request_options": options["model_request_options"],
            "task_fingerprint": task.fingerprint, "image": task.environment.revision,
            "status": "executor_returned", "cleanup_verified": True, "required_outputs_collected": True,
            "public_inputs": [{"path": "inputs/source.txt", "content_digest": task.inputs[0].content_digest}],
            "source_result_sha256": sha(trajectory), "broker_usage_sha256": sha(broker_receipt),
            "outputs": [{"path": "result.txt", "required": True,
                "status": "collected", "sha256": sha(source), "bytes": len(source)}]}
        path = root / "execution.json"
        path.write_text(json.dumps(report))
        return path
    def forward(self, raw):
        calls.append("judge")
        body = json.loads(raw)
        data = json.loads(body["messages"][1]["content"])
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"] if verdict else "invalid_submission", "arguments": json.dumps({"items": [
                {"id": "r", "verdict": verdict, "output_refs": [data["output_units"][0]["id"]],
                 "reason": "Retained"}]})}}]}}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}
    monkeypatch.setattr(module, "run_owned_native", run)
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    path = asyncio.run(module.execute_study(plan, {"task": task}, directory=tmp_path / "study",
        execution=execution, input_source_roots={"task": tmp_path}, judge=judge))
    report = json.loads(path.read_text())
    assert calls == ["execute", "judge", "execute", "execute", "judge", "execute", "judge", "execute", "judge", "execute", "judge"]
    assert report["promotion_eligible"] is False
    for index, entry in enumerate(report["cells"]):
        assert entry["review"]["status"] == ("reviewed" if verdict else "abstained")
        review = path.parent / entry["review"]["report"]
        assert sha(review.read_bytes()) == entry["review"]["sha256"]
        manifest = json.loads((review.parent / "manifest.json").read_text())
        assert manifest["selection_binding"]["cell_sha256"] == entry["sha256"]
        usage = json.loads((review.parent / "broker-usage.json").read_text())["shared_budget"]
        assert usage["roles"]["judge"]["reserved_calls"] == 1
        assert usage["roles"]["executor"]["reserved_calls"] == (2 if index == 1 else 1)
    from praxile.eval.five_arm_audit import audit_study
    original_study = path.read_bytes()
    audit_args = dict(directory=path.parent, study_sha256=sha(original_study), evaluator_root=judge["evaluator_root"])
    analysis = audit_study(plan, {"task": task}, **audit_args)
    assert analysis["complete_task_count"] == (1 if verdict == "preserved" else 0)
    assert analysis["reported_cells"] == 5
    assert analysis["metric"] == "llm_declared_requirement_preservation"
    assert analysis["promotion_eligible"] is False
    assert analysis["recorded_executor_conditions_checked"] is True
    assert analysis["broker_usage"]["total_tokens"] == {"input": 80, "output": 40}
    assert analysis["broker_usage"]["roles"]["executor"]["admitted_calls"] == 6
    assert analysis["broker_usage"]["roles"]["judge"]["admitted_calls"] == 5
    if verdict:
        assert all(row["verdict_counts"][verdict] == 1 for row in analysis["cell_reviews"])
    else:
        assert all(row["verdict_counts"] is None for row in analysis["cell_reviews"])
    from praxile.eval.five_arm_audit import main
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(task.to_dict()))
    assert main(["--study", str(path.parent), "--study-sha256", sha(original_study),
        "--task", str(task_file), "--evaluator-root", str(judge["evaluator_root"])]) == (0 if verdict == "preserved" else 1)
    calls_before = list(calls)
    # Even a rehashed, unselected attempt must retain the common executor settings.
    attempt_path = path.parent / "cells/1/attempt-1/lease/execution.json"
    cell_path = path.parent / "cells/1/cell.json"
    old_attempt, old_cell = attempt_path.read_bytes(), cell_path.read_bytes()
    changed_attempt = json.loads(old_attempt)
    changed_attempt["model_request_options"] = {"reasoning_effort": "high"}
    attempt_path.write_text(json.dumps(changed_attempt))
    changed_cell = json.loads(old_cell)
    changed_cell["attempts"][1]["sha256"] = sha(attempt_path.read_bytes())
    cell_path.write_text(json.dumps(changed_cell))
    changed_study = json.loads(original_study)
    changed_study["cells"][1]["sha256"] = sha(cell_path.read_bytes())
    path.write_text(json.dumps(changed_study))
    try:
        with pytest.raises(ValueError, match="attempt differs from study executor conditions"):
            audit_study(plan, {"task": task}, **{**audit_args, "study_sha256": sha(path.read_bytes())})
    finally:
        path.write_bytes(original_study)
        cell_path.write_bytes(old_cell)
        attempt_path.write_bytes(old_attempt)
    for relative in ("reviews/0/manifest.json", "reviews/0/trajectory.json", "reviews/0/report.json",
                     "reviews/0/inputs.json", "reviews/0/broker-usage.json", "cells/0/cell.json",
                     "cells/1/attempt-1/lease/agent/praxile-broker-usage.json",
                     "cells/0/attempt-0/lease/outputs/result.txt"):
        target = path.parent / relative
        original = target.read_bytes()
        target.write_text("{}")
        try:
            with pytest.raises(ValueError):
                audit_study(plan, {"task": task}, **audit_args)
        finally:
            target.write_bytes(original)
    report["cells"][0].pop("review")
    path.write_text(json.dumps(report))
    incomplete = audit_study(plan, {"task": task}, **{**audit_args, "study_sha256": sha(path.read_bytes())})
    assert incomplete["complete_task_count"] == 0
    assert incomplete["reported_cells"] == 4
    assert all(value["familywise_ci95"] is None for value in incomplete["comparisons"].values())
    assert calls == calls_before


@pytest.mark.parametrize("defect", ["model", "requirements", "selection", "private"])
def test_invalid_judge_rejected_before_executor(tmp_path, monkeypatch, defect):
    task, plan, execution, judge, _ = fixture(tmp_path)
    if defect == "model":
        judge["model"] = "changed"
    elif defect == "requirements":
        (judge["evaluator_root"] / "requirements.json").write_text("{}")
    elif defect == "selection":
        execution.pop("terminal_selection")
    else:
        judge["public"] = False
    monkeypatch.setattr(module, "run_owned_native", lambda *args, **kwargs: pytest.fail("no dispatch"))
    with pytest.raises(ValueError):
        asyncio.run(module.execute_study(plan, {"task": task}, directory=tmp_path / "study",
            execution=execution, input_source_roots={"task": tmp_path}, judge=judge))
    assert not (tmp_path / "study").exists()


def test_study_cancellation_waits_for_judge_and_retains_execution(tmp_path, monkeypatch):
    import threading
    import time
    from praxile.eval.five_arm_plan import iter_schedule, plan_digest
    task, plan, execution, judge, _ = fixture(tmp_path)
    entered, cleaned = threading.Event(), threading.Event()
    async def cell_runner(plan, cell_id, task, *, directory, **kwargs):
        directory.mkdir(parents=True)
        path = directory / "cell.json"
        row = next(item for item in iter_schedule(plan) if item["cell_id"] == cell_id)
        path.write_text(json.dumps({"plan_digest": plan_digest(plan), "cell": row,
            "status": "executed_not_scored", "selected_attempt": 0, "selection": {"selected_attempt": 0}}))
        return path
    def review(*args, cancel_requested, directory, **kwargs):
        entered.set()
        deadline = time.monotonic() + 3
        while not cancel_requested():
            if time.monotonic() >= deadline:
                raise AssertionError("judge did not receive cancellation")
            time.sleep(0.001)
        directory.mkdir(parents=True)
        path = directory / "report.json"
        path.write_text('{"status":"abstained"}')
        cleaned.set()
        return path
    monkeypatch.setattr(module, "execute_cell", cell_runner)
    monkeypatch.setattr("praxile.eval.five_arm_review.review_selected_cell", review)
    root = tmp_path / "study"
    async def exercise():
        running = asyncio.create_task(module.execute_study(plan, {"task": task}, directory=root,
            execution=execution, input_source_roots={"task": tmp_path}, judge=judge))
        async def wait_entered():
            while not entered.is_set():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait_entered(), 2)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    asyncio.run(exercise())
    assert cleaned.is_set()
    report = json.loads((root / "study.json").read_text())
    assert report["status"] == "interrupted"
    assert len(report["cells"]) == 1
    assert report["cells"][0]["status"] == "executed_not_scored"
    assert report["cells"][0]["review"]["status"] == "outcome_unknown"
