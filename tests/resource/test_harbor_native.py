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


def test_harbor_agent_usage_and_integrity(tmp_path):
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    kwargs = dict(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
                  base_url="http://127.0.0.1:18888/v1", model_name="fixture")
    with pytest.raises(ValueError, match="digest"):
        PraxileAgent(**kwargs, wheel_sha256="bad")
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
def test_harbor_owned_container_executes_native_worker(tmp_path, workbuddy):
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.config import EnvironmentConfig
    from harbor.models.trial.paths import TrialPaths

    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
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
import json
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
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
                workspace="/workspace", base_url="http://127.0.0.1:18888/v1",
                model_name="fixture", max_steps=2, timeout_seconds=90)
            await agent.setup(environment)
            if workbuddy:
                identity = await environment.exec("id -un", timeout_sec=10)
                assert identity.stdout.strip() == "dev"
            context = AgentContext()
            await agent.run("Summarize the available project.", environment, context)
            assert context.n_input_tokens == 12
            assert context.metadata["praxile"]["result"]["status"] == "completed"
            assert (paths.agent_dir / "praxile-result.json").is_file()
        finally:
            await environment.stop(delete=True)

    asyncio.run(exercise())
