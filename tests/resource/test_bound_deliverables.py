import pytest

from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.trajectory import TrajectoryLogger

pytestmark = [pytest.mark.resource, pytest.mark.runtime_resource, pytest.mark.sqlite_resource]


def contract():
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
            "outputs": [{"path": "report.json", "media_type": "application/json"}]}


@pytest.mark.parametrize("exists", [False, True])
def test_finish_rechecks_bound_requirements_without_agent_tool_call(tmp_path, monkeypatch, exists):
    if exists:
        (tmp_path / "report.json").write_text("{}")
    runtime = AgentRuntime(Config.load(tmp_path))
    monkeypatch.setattr(runtime, "_run_action_loop", lambda *args, **kwargs: ("completed", "model says completed, not cancelled"))
    result = runtime.run("Produce report", deliverable_contract=contract(), max_steps=1, use_experience=False)
    assert result["result"]["status"] == ("completed" if exists else "needs_human")
    assert result["deliverable_contract"] == contract()
    assert result["deliverable_verification"]["data"]["contract_binding"] == "run_argument"
    assert result["deliverable_contract_sha256"] == result["deliverable_verification"]["data"]["contract_sha256"]
    actions = [item for item in result["actions"] if item["action_type"] == "deliverable_verification"]
    assert len(actions) == 1
    assert result["cost"]["tool_calls"] == 0


@pytest.mark.parametrize("changed", [False, True])
def test_resume_restores_frozen_contract_and_rejects_replacement(tmp_path, monkeypatch, changed):
    runtime = AgentRuntime(Config.load(tmp_path))
    runtime.store.initialize(runtime.config)
    logger = TrajectoryLogger("Produce report", {})
    logger.data["deliverable_contract"] = contract()
    runtime.store.write_checkpoint({"task_id": logger.task_id, "trajectory": logger.data,
        "context": {"task": "Produce report"}, "messages": [{"role": "user", "content": "frozen"}]})
    called = []
    def loop(*args, **kwargs):
        called.append(runtime.tools.deliverable_contract)
        return "completed", "stub"
    monkeypatch.setattr(runtime, "_run_action_loop", loop)
    monkeypatch.setattr(runtime, "_finish_run", lambda logger, *args, **kwargs: logger.data)
    replacement = contract()
    replacement["outputs"][0]["required"] = False
    if changed:
        with pytest.raises(ValueError, match="cannot change the deliverable contract"):
            runtime.run("", resume=logger.task_id, deliverable_contract=replacement)
        assert not called
    else:
        runtime.run("", resume=logger.task_id)
        assert called == [contract()]
