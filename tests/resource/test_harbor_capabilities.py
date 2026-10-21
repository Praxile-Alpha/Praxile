import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")
from praxile.adapters.harbor_native import PraxileAgent

pytestmark = pytest.mark.resource


def make_agent(tmp_path, profiles):
    wheel = tmp_path / "praxile-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    return PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), base_url="http://localhost/v1",
        model_name="fixture", required_capabilities=profiles)


@pytest.mark.parametrize("profiles", ["xlsx_values.v1", ["unknown"], ["xlsx_values.v1"] * 2, [None]])
def test_invalid_required_profiles_rejected(tmp_path, profiles):
    with pytest.raises(ValueError):
        make_agent(tmp_path, profiles)


@pytest.mark.parametrize("status", ["passed", "failed", "malformed", "wrong_profile", "nonzero", "missing_checks"])
def test_setup_checks_worker_before_run(tmp_path, monkeypatch, status):
    agent = make_agent(tmp_path, ["xlsx_values.v1"])
    plain = make_agent(tmp_path, None)
    assert agent.version() != plain.version()
    assert agent.remote_root != plain.remote_root
    assert agent.worker_environment["required_capabilities"] == ["xlsx_values.v1"]
    run = AsyncMock()
    monkeypatch.setattr(agent, "_run_with_endpoint", run)
    with pytest.raises(RuntimeError, match="setup has not passed"):
        asyncio.run(agent.run("task", None, None))
    run.assert_not_called()
    commands = []
    async def execute(command, **kwargs):
        commands.append((command, kwargs))
        if "praxile.capability_probe" in command:
            report = {"schema_version": "praxile.capability_probe.v1", "profile": "xlsx_values.v1",
                      "status": "failed" if status == "failed" else "passed"}
            if status != "missing_checks":
                report.update(checks=["create", "read", "opener", "literal_roundtrip"],
                              packages={"openpyxl": "3.1.5", "et-xmlfile": "2.0.0", "defusedxml": "0.7.1"})
            if status == "wrong_profile":
                report["profile"] = "wrong"
            return SimpleNamespace(return_code=1 if status == "nonzero" else 0,
                stdout="bad JSON" if status == "malformed" else json.dumps(report))
        return SimpleNamespace(return_code=0, stdout="")
    environment = SimpleNamespace(exec=execute, upload_file=AsyncMock())
    if status == "passed":
        asyncio.run(agent.setup(environment))
        asyncio.run(agent.run("task", environment, None))
        run.assert_awaited_once()
        record = json.loads((tmp_path / "praxile-capabilities.json").read_text())
        assert record["worker_environment_sha256"] == agent.worker_environment_sha256
    else:
        with pytest.raises(RuntimeError, match="before model execution"):
            asyncio.run(agent.setup(environment))
        with pytest.raises(RuntimeError, match="setup has not passed"):
            asyncio.run(agent.run("task", environment, None))
        run.assert_not_called()
        assert (tmp_path / "praxile-capability-failure.json").is_file()
    probe, = [item for item in commands if "praxile.capability_probe" in item[0]]
    assert probe[1]["timeout_sec"] == 60 and "/venv/bin/python" in probe[0]


def test_renderer_failure_does_not_unlock_required_capability_run(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, ["xlsx_values.v1"])
    agent.renderer_executable = "/missing/renderer"
    run = AsyncMock()
    monkeypatch.setattr(agent, "_run_with_endpoint", run)

    async def execute(command, **kwargs):
        if "praxile.capability_probe" in command:
            return SimpleNamespace(return_code=0, stdout=json.dumps({
                "schema_version": "praxile.capability_probe.v1", "profile": "xlsx_values.v1",
                "status": "passed", "checks": ["create", "read", "opener", "literal_roundtrip"],
                "packages": {"openpyxl": "3.1.5", "et-xmlfile": "2.0.0", "defusedxml": "0.7.1"}}))
        return SimpleNamespace(return_code=1 if "/missing/renderer" in command else 0, stdout="")

    environment = SimpleNamespace(exec=execute, upload_file=AsyncMock())
    with pytest.raises(RuntimeError, match="renderer preflight failed"):
        asyncio.run(agent.setup(environment))
    with pytest.raises(RuntimeError, match="setup has not passed"):
        asyncio.run(agent.run("task", environment, None))
    run.assert_not_called()


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not all(os.environ.get(name) for name in (
    "PRAXILE_TEST_WHEEL", "PRAXILE_TEST_LEASE_IMAGE", "PRAXILE_TEST_XLSX_WHEELS")), reason="explicit Docker fixtures required")
def test_real_offline_worker_capability(tmp_path):
    from praxile.eval.harbor_lease import HarborDockerLease
    wheel = Path(os.environ["PRAXILE_TEST_WHEEL"]).resolve()
    dependencies = [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sorted(Path(os.environ["PRAXILE_TEST_XLSX_WHEELS"]).resolve().glob("*.whl"))]
    assert len(dependencies) == 3
    agent = PraxileAgent(logs_dir=tmp_path, wheel_path=str(wheel), workspace="/workspace",
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(), base_url="http://127.0.0.1:1/v1",
        model_name="never-called", required_capabilities=["xlsx_values.v1"], dependency_wheels=dependencies)
    async def exercise():
        async with HarborDockerLease(tmp_path, image=os.environ["PRAXILE_TEST_LEASE_IMAGE"]) as lease:
            await agent.setup(lease._environment)
            record = json.loads((tmp_path / "praxile-capabilities.json").read_text())
            assert record["reports"][0]["status"] == "passed"
            assert record["reports"][0]["model_called"] is False
            assert agent._capabilities_verified is True
            system = await lease.execute(["python3", "-I", "-c", "import importlib.util; assert importlib.util.find_spec('openpyxl') is None"], timeout=10)
            assert system.returncode == 0
    asyncio.run(exercise())
