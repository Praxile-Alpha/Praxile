import asyncio
import hashlib
import json

import pytest

from praxile.eval import five_arm_execution as module
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference
from praxile.eval.five_arm_plan import ARMS, IDENTITIES, compile_plan, iter_schedule
from praxile.eval.v2.candidate import ContextCandidate


def setup(tmp_path, arm):
    plan = compile_plan({"study_id": "dispatch", "task_ids": ["task"], "heldout_task_ids": ["hidden"],
        "repetitions": 1, "repeat_attempts": 2, "identities": {key: "sha256:" + "a" * 64 for key in IDENTITIES},
        "budget": {"total_tokens": 500, "model_calls": 4, "wall_seconds": 60}, "arm_order": list(ARMS)})
    cell = next(row for row in iter_schedule(plan) if row["arm"] == arm)
    task = BenchmarkTask(task_id="task", benchmark="fixture", dataset_revision="1", split="development",
        instruction="checklist", environment=EnvironmentSpec("harbor", "fixture", "python@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("judge", "1", "PRIVATE", "sha256:" + "f" * 64))
    candidate = ContextCandidate(candidate_id="dev", version="1", title="Fixture", candidate_type="context_policy",
        context_item={"content": "PRIVATE_CONTEXT"}, confidence=0.0, evidence_refs=("event:fixture",),
        expected_effect={"hypothesis": "fixture"}, applies_to={"repositories": ["docs"],
        "task_signals": ["checklist"], "does_not_apply_when": ["unrelated"]})
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate.to_dict()))
    kwargs = dict(directory=tmp_path / "cell", wheel=tmp_path / "fixture.whl", wheel_sha256="a" * 64,
        model="fixture", base_url="https://provider.invalid/v1", credential_file=tmp_path / "key", output_budget=100,
        development_candidate_file=path, development_candidate_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        repository="docs")
    return plan, cell, task, kwargs


@pytest.mark.parametrize("arm", ARMS)
def test_dispatch_maps_five_arms_and_shares_quota(tmp_path, monkeypatch, arm):
    plan, cell, task, kwargs = setup(tmp_path, arm)
    budgets = []
    async def run(task, **options):
        assert options["task_local_memory_enabled"] is cell["task_local_enabled"]
        assert ("development_candidate_file" in options) is cell["experience_enabled"]
        assert "persist_workspace" not in options
        budget = options["shared_chat_budget"]
        budgets.append(budget)
        budget.reserve("executor", 10)
        root = options["state_root"]
        root.mkdir()
        path = root / "execution.json"
        path.write_text(json.dumps({"status": "executor_returned", "cleanup_verified": True}))
        return path
    monkeypatch.setattr(module, "run_owned_native", run)
    path = asyncio.run(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
    report = json.loads(path.read_text())
    assert report["status"] == "executed_not_scored"
    assert len(budgets) == cell["max_attempts"]
    assert all(item is budgets[0] for item in budgets)
    assert report["shared_budget"]["remaining_calls"] == 4 - cell["max_attempts"]
    assert report["shared_budget"]["remaining_output"] == 100 - 10 * cell["max_attempts"]
    assert report["selected_attempt"] is None
    assert report["promotion_eligible"] is False
    assert "PRIVATE" not in path.read_text()
    for attempt in report["attempts"]:
        assert attempt["sha256"] == hashlib.sha256((path.parent / attempt["report"]).read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        asyncio.run(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
    assert len(budgets) == cell["max_attempts"]


def test_unknown_attempt_is_persisted_and_never_retried(tmp_path, monkeypatch):
    plan, cell, task, kwargs = setup(tmp_path, "budget_matched_repeat")
    calls = []
    async def run(task, **options):
        calls.append(1)
        options["shared_chat_budget"].reserve("executor", 10)
        raise RuntimeError("PRIVATE_ERROR")
    monkeypatch.setattr(module, "run_owned_native", run)
    with pytest.raises(RuntimeError):
        asyncio.run(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
    report = json.loads((kwargs["directory"] / "cell.json").read_text())
    assert len(calls) == 1
    assert report["status"] == "failed"
    assert report["attempts"][0]["status"] == "outcome_unknown"
    assert report["shared_budget"]["remaining_calls"] == 3
    assert "PRIVATE_ERROR" not in json.dumps(report)


def test_cancel_waits_for_runner_cleanup_and_records_interruption(tmp_path, monkeypatch):
    plan, cell, task, kwargs = setup(tmp_path, "budget_matched_repeat")
    cleaned = []
    async def exercise():
        entered = asyncio.Event()
        async def run(task, **options):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.append(True)
        monkeypatch.setattr(module, "run_owned_native", run)
        running = asyncio.create_task(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    asyncio.run(exercise())
    report = json.loads((kwargs["directory"] / "cell.json").read_text())
    assert cleaned == [True]
    assert report["status"] == "interrupted"
    assert len(report["attempts"]) == 1


def test_exhausted_cell_does_not_reset_quota_for_repeat(tmp_path, monkeypatch):
    plan, cell, task, kwargs = setup(tmp_path, "budget_matched_repeat")
    async def run(task, **options):
        options["shared_chat_budget"].reserve("executor", 100)
        root = options["state_root"]
        root.mkdir()
        path = root / "execution.json"
        path.write_text(json.dumps({"status": "executor_returned", "cleanup_verified": True}))
        return path
    monkeypatch.setattr(module, "run_owned_native", run)
    path = asyncio.run(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
    report = json.loads(path.read_text())
    assert report["status"] == "budget_exhausted"
    assert len(report["attempts"]) == 1


@pytest.mark.parametrize("defect", ["candidate", "broker", "budget", "cell", "tamper"])
def test_invalid_dispatch_does_not_create_cell(tmp_path, defect):
    plan, cell, task, kwargs = setup(tmp_path, "experience_only")
    if defect == "candidate":
        kwargs["development_candidate_file"] = None
    elif defect == "broker":
        kwargs["credential_file"] = None
    elif defect == "budget":
        kwargs["output_budget"] = 501
    elif defect == "cell":
        cell["cell_id"] = "unknown"
    else:
        plan["arms"]["experience_only"]["experience_enabled"] = False
    with pytest.raises(ValueError):
        asyncio.run(module.execute_cell(plan, cell["cell_id"], task, **kwargs))
    assert not kwargs["directory"].exists()
