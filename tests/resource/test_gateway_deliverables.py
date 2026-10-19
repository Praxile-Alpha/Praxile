import pytest

from praxile.gateway import GatewayApp, GatewayError
from praxile.runtime import AgentRuntime

pytestmark = [pytest.mark.resource, pytest.mark.gateway_resource, pytest.mark.sqlite_resource]


def contract():
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
            "outputs": [{"path": "report.json", "media_type": "application/json"}]}


@pytest.mark.parametrize("path", ["/run", "/api/runs", "/api/runs/jobs"])
def test_web_run_preserves_contract_and_enforces_final_verification(tmp_path, monkeypatch, path):
    monkeypatch.setattr(AgentRuntime, "_run_action_loop", lambda *args, **kwargs: ("completed", "model claim"))
    app = GatewayApp(tmp_path)
    request = {"task": "Produce report", "max_steps": 1, "deliverable_contract": contract()}
    result = app.dispatch("POST", path, payload=request)
    if path.endswith("jobs"):
        job = app.jobs.get(result["job_id"])
        request["deliverable_contract"]["outputs"][0]["required"] = False
        job.thread.join(timeout=10)
        assert not job.thread.is_alive()
        assert job.error is None
        assert job.payload["deliverable_contract"] == contract()
        result = job.result
    assert result["status"] == "needs_human"
    assert len(result["deliverable_contract_sha256"]) == 64
    assert result["deliverable_verification"]["data"]["contract_binding"] == "run_argument"
    detail = app.dispatch("GET", f"/api/runs/{result['task_id']}")
    assert detail["deliverable_contract"] == contract()
    assert detail["deliverable_contract_sha256"] == result["deliverable_contract_sha256"]


@pytest.mark.parametrize("path", ["/run", "/api/runs", "/api/runs/jobs"])
@pytest.mark.parametrize("invalid", ["/etc/passwd", {}, {"source": "hidden_rubric"}])
def test_invalid_contract_rejected_before_execution_or_queue(tmp_path, monkeypatch, path, invalid):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached runtime")
    monkeypatch.setattr(AgentRuntime, "run", forbidden)
    app = GatewayApp(tmp_path)
    with pytest.raises(GatewayError) as error:
        app.dispatch("POST", path, payload={"task": "Produce report", "deliverable_contract": invalid})
    assert error.value.status == 400
    assert app.jobs.list() == []


@pytest.mark.parametrize("async_mode", [False, True])
def test_chat_retry_inherits_contract_and_rejects_removal(tmp_path, monkeypatch, async_mode):
    monkeypatch.setattr(AgentRuntime, "_run_action_loop", lambda *args, **kwargs: ("completed", "model claim"))
    app = GatewayApp(tmp_path)
    session = app.dispatch("POST", "/api/chat/sessions", payload={"title": "Report"})
    base = f"/api/chat/sessions/{session['session_id']}"
    suffix = "-async" if async_mode else ""
    def outcome(response):
        if not async_mode:
            return response["run"]
        job = app.jobs.get(response["job"]["job_id"])
        job.thread.join(timeout=10)
        assert not job.thread.is_alive()
        assert job.error is None
        return job.result
    first = outcome(app.dispatch("POST", base + "/message" + suffix,
        payload={"task": "Produce report", "deliverable_contract": contract(), "max_steps": 1}))
    second = outcome(app.dispatch("POST", base + "/retry" + suffix, payload={"max_steps": 1}))
    assert first["status"] == second["status"] == "needs_human"
    assert first["deliverable_contract_sha256"] == second["deliverable_contract_sha256"]
    with pytest.raises(GatewayError, match="Retry cannot change"):
        app.dispatch("POST", base + "/retry" + suffix, payload={"deliverable_contract": None})
