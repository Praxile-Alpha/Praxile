import asyncio
import json

import pytest

from praxile.eval import native_pair_run as runner
from praxile.eval.native_pair import encoded, prepare_pair
from praxile.eval.v2.candidate import ContextCandidate


@pytest.fixture
def pair(tmp_path):
    candidate = ContextCandidate(candidate_id="pair", version="1", title="Focused check",
        candidate_type="context_policy", context_item={"content": "Check page count."},
        confidence=0, evidence_refs=("event:source",), expected_effect={"hypothesis": "Avoid invalid output"},
        applies_to={"repositories": ["docs"], "task_signals": ["one-page"],
                    "does_not_apply_when": ["multi-page"]}).to_dict()
    job = {"job_name": "paired", "jobs_dir": str(tmp_path / "jobs"),
        "tasks": [{"path": str(tmp_path / "task")}], "datasets": [],
        "agents": [{"import_path": "praxile.adapters.harbor_native:PraxileAgent", "model_name": "fixture",
                    "kwargs": {"max_steps": 20, "timeout_seconds": 60}}]}
    root = tmp_path / "pair"
    root.mkdir()
    plan, arms = prepare_pair(job, candidate, repository="docs", output_dir=root)
    for name, value in [("manifest.json", plan), ("candidate-asset.json", candidate),
                        *[(arm + ".json", config) for arm, config in arms.items()]]:
        (root / name).write_bytes(encoded(value))
    return root


def backend(monkeypatch, callback):
    calls = []
    class FakeJob:
        def __init__(self, config):
            self.config = config
        async def run(self):
            calls.append(self.config["job_name"])
            callback(self.config)
    async def create(config):
        return FakeJob(config)
    monkeypatch.setattr(runner, "harbor_backend", lambda arms: (create, arms, "fixture"))
    return calls


def test_executes_in_order_and_refuses_duplicate_call(pair, monkeypatch):
    calls = backend(monkeypatch, lambda config: None)
    state = asyncio.run(runner.run_pair(pair))
    assert calls == ["paired-baseline", "paired-candidate"]
    assert state["status"] == "jobs_returned"
    assert not state["controlled_experiment_verified"] and not state["promotion_eligible"]
    with pytest.raises(FileExistsError):
        asyncio.run(runner.run_pair(pair))
    assert len(calls) == 2


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_failure_or_cancellation_never_starts_second_arm(pair, monkeypatch, error):
    def fail(config):
        raise error("private endpoint details")
    calls = backend(monkeypatch, fail)
    with pytest.raises(error):
        asyncio.run(runner.run_pair(pair))
    state = json.loads((pair / "execution.json").read_text())
    assert state["status"] == ("failed" if error is RuntimeError else "interrupted")
    assert state["arms"]["baseline"]["status"] == state["status"]
    assert state["arms"]["baseline"]["resource_cleanup_verified"] is False
    assert calls == ["paired-baseline"]
    assert "private endpoint" not in (pair / "execution.json").read_text()


@pytest.mark.parametrize("name", ["manifest.json", "baseline.json", "candidate.json", "candidate-asset.json"])
def test_tampered_inputs_rejected_before_backend(pair, monkeypatch, name):
    calls = backend(monkeypatch, lambda config: None)
    value = json.loads((pair / name).read_text())
    value["tampered"] = True
    (pair / name).write_text(json.dumps(value))
    with pytest.raises(ValueError):
        asyncio.run(runner.run_pair(pair))
    assert not calls and not (pair / "execution.json").exists()


def test_changed_inputs_between_arms_stop_candidate(pair, monkeypatch):
    def change(config):
        (pair / "candidate-asset.json").write_text("{}")
    calls = backend(monkeypatch, change)
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(runner.run_pair(pair))
    assert calls == ["paired-baseline"]
    state = json.loads((pair / "execution.json").read_text())
    assert state["arms"]["baseline"]["status"] == "job_returned"
    assert state["stopped_at_arm"] == "candidate"


def test_execute_flag_required_without_starting_anything(pair, monkeypatch):
    monkeypatch.setattr("sys.argv", ["native_pair_run", "--pair-dir", str(pair)])
    with pytest.raises(SystemExit):
        runner.main()
    assert not (pair / "execution.json").exists()


def test_v2_local_snapshot_stops_changed_task_between_arms(pair, monkeypatch):
    import hashlib
    from praxile.eval.native_input_snapshot import snapshot_inputs
    task = pair.parent / "task"
    task.mkdir()
    (task / "instruction.md").write_text("initial")
    wheel = pair.parent / "worker.whl"
    wheel.write_bytes(b"fixture")
    base = json.loads((pair / "baseline.json").read_text())
    base["job_name"] = "paired"
    base["agents"][0]["kwargs"].update(wheel_path=str(wheel), wheel_sha256=hashlib.sha256(b"fixture").hexdigest())
    candidate = json.loads((pair / "candidate-asset.json").read_text())
    plan, arms = prepare_pair(base, candidate, repository="docs", output_dir=pair)
    plan.update(schema_version="praxile.native_pair_plan.v2", local_inputs=snapshot_inputs(base))
    (pair / "manifest.json").write_bytes(encoded(plan))
    for arm, config in arms.items():
        (pair / (arm + ".json")).write_bytes(encoded(config))
    calls = backend(monkeypatch, lambda config: (task / "instruction.md").write_text("changed"))
    with pytest.raises(ValueError, match="between arms"):
        asyncio.run(runner.run_pair(pair))
    assert calls == ["paired-baseline"]
    assert json.loads((pair / "execution.json").read_text())["local_inputs_frozen"] is True
