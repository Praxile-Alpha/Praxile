import pytest
import json

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


def test_final_state_evidence_survives_trajectory_storage(tmp_path, monkeypatch):
    runtime = AgentRuntime(Config.load(tmp_path))
    monkeypatch.setattr(runtime, "_run_action_loop", lambda *args, **kwargs: ("completed", "stub"))
    request = {"schema_version": "praxile.deliverable_contract.v2", "source": "user_request",
               "outputs": [], "state_equals": {"browser.session_active": False}}
    result = runtime.run("Keep browser closed", deliverable_contract=request, max_steps=1, use_experience=False)
    assert result["result"]["status"] == "completed"
    report = result["deliverable_verification"]["data"]
    stored = runtime.store.get_trajectory(result["task_id"])
    assert stored["deliverable_verification"]["data"]["state_evidence"] == report["state_evidence"]
    assert stored["deliverable_verification"]["data"]["state_evidence_sha256"] == report["state_evidence_sha256"]
    evidence = report["state_evidence"]
    assert evidence["source"] == "browser_session_observer"
    assert evidence["observed_at"]
    assert evidence["fields"] == [{"name": "browser.session_active", "availability": "observed",
        "value": False, "value_sha256": evidence["fields"][0]["value_sha256"]}]


def test_finish_gate_allows_repair_within_existing_budget(tmp_path, monkeypatch):
    runtime = AgentRuntime(Config.load(tmp_path))
    actions = iter([
        {"type": "finish", "summary": "premature completion"},
        {"type": "edit_file", "path": "report.json", "content": "{}"},
        {"type": "finish", "summary": "repaired"},
    ])
    prompts = []
    def chat(*args, **kwargs):
        prompts.append(json.dumps(kwargs.get("messages", args), default=str))
        return {"content": json.dumps(next(actions)), "usage": {}, "route": {}}
    monkeypatch.setattr(runtime.router, "chat", chat)
    result = runtime.run("Produce report", deliverable_contract=contract(), max_steps=3, use_experience=False)
    assert result["result"]["status"] == "completed"
    gates = [a for a in result["actions"] if a["action_type"] == "deliverable_finish_gate"]
    assert [a["status"] for a in gates] == ["failure", "success"]
    assert any("Completion rejected" in p for p in prompts)
    assert result["deliverable_verification"]["status"] == "success"


def test_unconfigured_renderer_cannot_pass_bound_docx_completion(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    docx.Document().save(tmp_path / "checklist.docx")
    request = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "checklist.docx", "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "structure": {"max_pages": 1}}]}
    runtime = AgentRuntime(Config.load(tmp_path))
    monkeypatch.setattr(runtime.router, "chat", lambda *a, **k: {
        "content": json.dumps({"type": "finish", "summary": "claimed one page"}), "usage": {}, "route": {}})
    result = runtime.run("Produce a one-page checklist", deliverable_contract=request, max_steps=1, use_experience=False)
    assert result["result"]["status"] == "needs_human"
    assert result["deliverable_verification"]["status"] == "failure"
    assert any(a["action_type"] == "deliverable_finish_gate" and a["status"] == "failure" for a in result["actions"])
