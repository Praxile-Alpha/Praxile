"""Optional Harbor contract and Docker lifecycle tests.

Set PRAXILE_TEST_WHEEL to a built wheel to opt into the real Docker test.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("harbor")
from harbor.models.agent.context import AgentContext
from praxile.adapters.harbor_native import PraxileAgent, WorkBuddyPraxileAgent

pytestmark = [pytest.mark.resource, pytest.mark.integration]


@pytest.mark.parametrize("cancel", [False, True])
def test_broker_is_revoked_when_run_fails(tmp_path, monkeypatch, cancel):
    from types import SimpleNamespace
    import praxile.eval.credential_broker as module
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    key = tmp_path / "private-key"
    key.write_text("host-only-fixture")
    key.chmod(0o600)
    states = []
    class Broker:
        capability = "temporary-capability"
        def __init__(self, **kwargs):
            assert kwargs["key"] == "host-only-fixture"
        def start(self):
            return "127.0.0.1", 12345
        def revoke(self):
            states.append("revoked")
        def close(self):
            states.append("closed")
    monkeypatch.setattr(module, "ChatCredentialBroker", Broker)
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        base_url="https://provider.invalid/v1", model_name="fixture", credential_file=str(key),
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    class Environment:
        async def upload_file(self, path, remote):
            assert path.read_text() == "temporary-capability"
        async def exec(self, command, **kwargs):
            assert "temporary-capability" not in command and "host-only-fixture" not in command
            return SimpleNamespace(return_code=0)
    async def fail(*args, **kwargs):
        assert kwargs["endpoint"] == "http://host.docker.internal:12345/v1"
        raise asyncio.CancelledError() if cancel else RuntimeError("fixture failure")
    monkeypatch.setattr(agent, "_run_with_endpoint", fail)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        asyncio.run(agent.run("public task", Environment(), AgentContext()))
    assert states == ["revoked", "closed"]


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "exit"])
@pytest.mark.parametrize("result_exists", [False, True])
def test_failed_execution_preserves_primary_error_and_available_evidence(tmp_path, failure, result_exists):
    from types import SimpleNamespace
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        base_url="http://localhost/v1", model_name="fixture", wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())

    class Environment:
        async def upload_file(self, *args):
            pass

        async def exec(self, *args, **kwargs):
            if failure == "timeout":
                raise TimeoutError("secret-exception-text")
            if failure == "cancelled":
                raise asyncio.CancelledError("secret-exception-text")
            return SimpleNamespace(return_code=1)

        async def download_file(self, source, target):
            target.write_text("partial")
            if not result_exists:
                raise FileNotFoundError("secret-download-text")
            target.write_text(json.dumps({"schema_version": "praxile.native_benchmark_run.v1",
                "trajectory": {"task_id": "failed-task"}, "experience_mode": "fresh_disabled"}))

    expected = {"timeout": TimeoutError, "cancelled": asyncio.CancelledError, "exit": RuntimeError}[failure]
    with pytest.raises(expected):
        asyncio.run(agent.run("public task", Environment(), AgentContext()))
    text = (tmp_path / "praxile-execution.json").read_text()
    record = json.loads(text)
    assert record["status"] == ("cancelled" if failure == "cancelled" else "failed")
    assert record["error_type"] == expected.__name__
    assert record["result_available"] is result_exists
    assert record["elapsed_seconds"] >= 0
    assert "secret-" not in text
    assert (tmp_path / "praxile-result.json").exists() is result_exists
    assert not (tmp_path / "praxile-result.partial.json").exists()


def test_successful_process_with_invalid_result_is_collection_failure(tmp_path):
    from types import SimpleNamespace
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        base_url="http://localhost/v1", model_name="fixture", wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())

    class Environment:
        async def upload_file(self, *args):
            pass

        async def exec(self, *args, **kwargs):
            return SimpleNamespace(return_code=0)

        async def download_file(self, source, target):
            target.write_text("{}")

    with pytest.raises(ValueError, match="invalid native"):
        asyncio.run(agent.run("public task", Environment(), AgentContext()))
    record = json.loads((tmp_path / "praxile-execution.json").read_text())
    assert record["status"] == "collection_failed"
    assert record["result_available"] is False
    assert not (tmp_path / "praxile-result.json").exists()
    assert not (tmp_path / "praxile-result.partial.json").exists()


def test_native_worker_is_supervised_and_deadline_evidence_is_retained(tmp_path):
    from types import SimpleNamespace
    import shlex
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        base_url="http://localhost/v1", model_name="fixture", timeout_seconds=30,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    class Environment:
        async def upload_file(self, *args):
            pass
        async def exec(self, command, **kwargs):
            argv = shlex.split(command)
            assert argv[2] == "praxile.eval.process_supervisor"
            assert argv[argv.index("--timeout") + 1] == "30"
            assert "praxile.eval.native_worker" in argv
            assert kwargs["timeout_sec"] == 40
            return SimpleNamespace(return_code=124)
        async def download_file(self, source, target):
            if source.endswith("/supervision.json"):
                target.write_text(json.dumps({"schema_version": "praxile.worker_supervision.v1",
                    "status": "timed_out", "returncode": 124, "untrusted_extra": "secret"}))
            else:
                raise FileNotFoundError()
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run("public task", Environment(), AgentContext()))
    record = json.loads((tmp_path / "praxile-execution.json").read_text())
    assert record["worker_supervision"] == {"status": "timed_out", "returncode": 124}
    assert not record["result_available"]
    assert "secret" not in json.dumps(record)
    assert not (tmp_path / "praxile-supervision.partial.json").exists()


@pytest.mark.parametrize("request_fails", [False, True])
def test_host_cancellation_requests_worker_stop_without_replacing_primary_error(tmp_path, request_fails):
    from types import SimpleNamespace
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        base_url="http://localhost/v1", model_name="fixture",
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    class Environment:
        requested = False
        async def upload_file(self, *args):
            pass
        async def exec(self, command, **kwargs):
            if "praxile.eval.process_supervisor" in command:
                raise asyncio.CancelledError("primary cancellation")
            assert ".touch()" in command
            self.requested = not request_fails
            return SimpleNamespace(return_code=1 if request_fails else 0)
        async def download_file(self, source, target):
            if source.endswith("/supervision.json") and self.requested:
                target.write_text(json.dumps({"schema_version": "praxile.worker_supervision.v1",
                                              "status": "cancelled", "returncode": 143}))
            else:
                raise FileNotFoundError()
    with pytest.raises(asyncio.CancelledError, match="primary cancellation"):
        asyncio.run(agent.run("public task", Environment(), AgentContext()))
    record = json.loads((tmp_path / "praxile-execution.json").read_text())
    assert record["worker_cancel"]["requested"] is not request_fails
    assert record["worker_cancel"]["termination_confirmed"] is not request_fails
    assert record["status"] == "cancelled"


def test_harbor_agent_usage_and_integrity(tmp_path):
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    kwargs = dict(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
                  base_url="http://127.0.0.1:18888/v1", model_name="fixture")
    with pytest.raises(ValueError, match="digest"):
        PraxileAgent(**kwargs, wheel_sha256="bad")
    for invalid in ({"task_body_file": "task.txt"},
                    {"task_body_file": "../task.txt", "task_body_sha256": "a" * 64},
                    {"task_body_file": "task.txt", "task_body_sha256": "bad"}):
        with pytest.raises(ValueError, match="task body"):
            PraxileAgent(**kwargs, wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), **invalid)
    agent = PraxileAgent(**kwargs, wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())
    report = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
              "trajectory": {"task_id": "fixture", "model_routing": {"calls": [
                  {"usage": {"prompt_tokens": 12, "completion_tokens": 7}}, {"usage": {}}
              ]}}}
    (tmp_path / "praxile-result.json").write_text(json.dumps(report))
    context = AgentContext()
    agent.populate_context_post_run(context)
    assert context.n_input_tokens is None
    assert context.cost_usd is None
    report["trajectory"]["model_routing"]["calls"].pop()
    (tmp_path / "praxile-result.json").write_text(json.dumps(report))
    agent.populate_context_post_run(context)
    assert context.n_input_tokens == 12
    assert context.n_output_tokens == 7


@pytest.mark.slow
@pytest.mark.timeout(300)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_WHEEL"), reason="explicit built wheel and Docker required")
@pytest.mark.parametrize("workbuddy", [False, True])
@pytest.mark.parametrize("cancel_run", [False, True])
def test_harbor_owned_container_executes_native_worker(tmp_path, workbuddy, cancel_run, monkeypatch, broker_mode=False):
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths

    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    credential = tmp_path / "host-key"
    broker_instances = []
    if broker_mode:
        from praxile.eval.credential_broker import ChatCredentialBroker
        credential.write_text("host-only-fixture-secret")
        credential.chmod(0o600)
        def forward(broker, body):
            assert broker._key == "host-only-fixture-secret"
            broker_instances.append(broker)
            return {"choices": [{"message": {"content": json.dumps({"type": "finish", "summary": "fixture completed", "status": "completed"})}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 7}}
        monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    environment_dir = tmp_path / "environment"
    environment_dir.mkdir()
    paths = TrialPaths(tmp_path / "trial")
    paths.mkdir()
    environment = DockerEnvironment(
        environment_dir=environment_dir, environment_name="praxile-native-smoke",
        session_id="praxile-native-" + tmp_path.name.lower().replace("_", "-")[-24:],
        trial_paths=paths,
        task_env_config=EnvironmentConfig(docker_image="python:3.12-slim", workdir="/workspace"),
    )
    server = tmp_path / "model.py"
    server.write_text('''from http.server import HTTPServer, BaseHTTPRequestHandler
import json, time
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        if self.path.startswith('/block/'):
            Path('/tmp/model-request-started').touch()
            time.sleep(120)
        body = json.dumps({'choices':[{'message':{'content':json.dumps({'type':'finish','summary':'fixture completed','status':'completed'})}}], 'usage':{'prompt_tokens':12,'completion_tokens':7}}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer(('127.0.0.1',18888),Handler).serve_forever()
''')

    async def exercise():
        try:
            await environment.start(force_build=False)
            await environment.upload_file(server, "/tmp/fixture_model.py")
            result = await environment.exec("mkdir -p /workspace && python3 /tmp/fixture_model.py >/tmp/fixture-model.log 2>&1 &", cwd="/", timeout_sec=10)
            assert result.return_code == 0
            agent_type = WorkBuddyPraxileAgent if workbuddy else PraxileAgent
            if workbuddy:
                environment.default_user = "dev"
            agent = agent_type(logs_dir=paths.agent_dir, wheel_path=str(wheel),
                wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
                workspace="/workspace", base_url=("https://provider.invalid/v1" if broker_mode else
                    "http://127.0.0.1:18888" + ("/block/v1" if cancel_run else "/v1")),
                credential_file=str(credential) if broker_mode else None,
                model_name="fixture", max_steps=2, timeout_seconds=90)
            await agent.setup(environment)
            if workbuddy:
                identity = await environment.exec("id -un", timeout_sec=10)
                assert identity.stdout.strip() == "dev"
            context = AgentContext()
            if cancel_run:
                running_agent = asyncio.create_task(agent.run("Summarize the available project.", environment, context))
                try:
                    async with asyncio.timeout(20):
                        while True:
                            observed = await environment.exec("test -f /tmp/model-request-started", timeout_sec=5)
                            if observed.return_code == 0:
                                break
                            if running_agent.done():
                                await running_agent
                                pytest.fail("worker ended before the model request")
                            await asyncio.sleep(.1)
                    running_agent.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(running_agent, timeout=25)
                    execution = json.loads((paths.agent_dir / "praxile-execution.json").read_text())
                    assert execution["status"] == "cancelled"
                    assert execution["worker_cancel"] == {"requested": True, "termination_confirmed": True}
                    assert execution["worker_supervision"] == {"status": "cancelled", "returncode": 143}
                    assert not execution["result_available"]
                    assert not (paths.agent_dir / "praxile-result.json").exists()
                finally:
                    if not running_agent.done():
                        running_agent.cancel()
                    await asyncio.gather(running_agent, return_exceptions=True)
                return
            await agent.run("Summarize the available project.", environment, context)
            if broker_mode:
                assert broker_instances and all(item.status()["revoked"] for item in broker_instances)
                assert all(item._thread is None for item in broker_instances)
                assert "host-only-fixture-secret" not in (paths.agent_dir / "praxile-result.json").read_text()
                remaining = await environment.exec(f"find {agent.remote_root} -maxdepth 1 -name 'capability_*'", timeout_sec=5)
                assert remaining.return_code == 0
                assert not (remaining.stdout or "").strip()
            assert context.n_input_tokens == 12
            assert context.metadata["praxile"]["result"]["status"] == "completed"
            assert (paths.agent_dir / "praxile-result.json").is_file()
            execution = json.loads((paths.agent_dir / "praxile-execution.json").read_text())
            assert execution["worker_supervision"] == {"status": "completed", "returncode": 0}
            # Exercise the shipped supervisor inside the container, including
            # a descendant retaining the Docker exec output pipe.
            import shlex
            python = agent.remote_root + "/venv/bin/python"
            status_path = agent.remote_root + "/timeout-smoke.json"
            program = ("import subprocess,sys,time; "
                       "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                       "time.sleep(60)")
            timeout_result = await environment.exec(shlex.join([
                python, "-m", "praxile.eval.process_supervisor", "--timeout", "0.5",
                "--status", status_path, "--", python, "-c", program]), timeout_sec=10)
            assert timeout_result.return_code == 124
            local_status = paths.agent_dir / "timeout-smoke.json"
            await environment.download_file(status_path, local_status)
            assert json.loads(local_status.read_text())["status"] == "timed_out"
            cancel_path = agent.remote_root + "/cancel-smoke"
            running = asyncio.create_task(environment.exec(shlex.join([
                python, "-m", "praxile.eval.process_supervisor", "--timeout", "30",
                "--status", agent.remote_root + "/supervision.json",
                "--cancel-file", cancel_path, "--", python, "-c", program]), timeout_sec=40))
            try:
                record = {}
                async with asyncio.timeout(10):
                    while True:
                        await agent._record_supervision(environment, record)
                        if record.get("worker_supervision", {}).get("status") == "running":
                            break
                        await asyncio.sleep(.1)
                await agent._cancel_worker(environment, cancel_path, record)
                assert record["worker_cancel"] == {"requested": True, "termination_confirmed": True}
                assert record["worker_supervision"]["status"] == "cancelled"
                assert (await asyncio.wait_for(running, timeout=5)).return_code == 143
            finally:
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        finally:
            await environment.stop(delete=True)

    asyncio.run(exercise())


@pytest.mark.slow
@pytest.mark.timeout(300)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_WHEEL"), reason="explicit built wheel and Docker required")
@pytest.mark.parametrize("workbuddy", [False, True])
def test_harbor_broker_native_worker(tmp_path, workbuddy, monkeypatch):
    test_harbor_owned_container_executes_native_worker(tmp_path, workbuddy, False, monkeypatch, broker_mode=True)
