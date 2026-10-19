import argparse
import importlib.metadata
import json
import subprocess
from types import SimpleNamespace

import pytest

from praxile.services import benchmark_health as health


def test_missing_prerequisites_do_not_invoke_processes_or_expose_key(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(health.importlib.metadata, "version", missing)
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    monkeypatch.setattr(health.subprocess, "run", lambda *a, **kw: pytest.fail("unexpected process"))
    monkeypatch.setenv("PRAXILE_BENCHMARK_API_KEY", "secret-fixture")
    report = health.inspect_benchmark("workbuddy")
    assert not report["infrastructure_available"]
    assert not report["execution_verified"]
    assert "secret-fixture" not in json.dumps(report)
    assert next(c for c in report["checks"] if c["name"] == "executor_credential")["status"] == "configured"


@pytest.mark.parametrize("online", [False, True])
def test_optional_daemon_probe_is_bounded_and_does_not_call_model(monkeypatch, online):
    monkeypatch.setattr(health.importlib.metadata, "version", lambda name: "0.18.0")
    monkeypatch.setattr(health.shutil, "which", lambda name: "/bin/docker")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] <= 15
        return SimpleNamespace(returncode=0, stdout=b'{"Version":"29.8.0","Os":"linux","Arch":"arm64"}', stderr=b"")
    monkeypatch.setattr(health.subprocess, "run", run)
    report = health.inspect_benchmark("workbuddy", online=online)
    assert report["infrastructure_available"] is online
    assert not report["execution_verified"]
    assert len(calls) == (2 if online else 1)
    if online:
        assert calls[-1] == ["/bin/docker", "version", "--format", "{{json .Server}}"]
        architecture = next(c for c in report["checks"] if c["name"] == "architecture")
        assert architecture["detail"]["docker_server_platform"] == "linux/arm64"
        assert not architecture["detail"]["image_platform_verified"]


def test_disk_probe_uses_existing_ancestor_without_initializing_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    directory = tmp_path / "not-created" / "project"
    report = health.inspect_benchmark("workbuddy", workspace=directory)
    disk = next(c for c in report["checks"] if c["name"] == "disk_access")
    assert disk["detail"]["filesystem_path"] == str(tmp_path.resolve())
    assert disk["detail"]["free_bytes"] >= 0
    assert disk["detail"]["required_bytes"] is None
    assert not directory.exists()


@pytest.mark.parametrize("payload", [b"not json", b"null", b"[]", b'{"Version":true}'])
def test_invalid_daemon_payload_does_not_claim_available(monkeypatch, payload):
    monkeypatch.setattr(health.importlib.metadata, "version", lambda name: "0.18.0")
    monkeypatch.setattr(health.shutil, "which", lambda name: "/bin/docker")
    monkeypatch.setattr(health.subprocess, "run", lambda *args, **kwargs:
        SimpleNamespace(returncode=0, stdout=payload, stderr=b""))
    assert not health.inspect_benchmark("workbuddy", online=True)["infrastructure_available"]


def test_import_and_daemon_timeout_are_reported_without_exception_text(monkeypatch):
    monkeypatch.setattr(health.importlib.metadata, "version", lambda name: "0.22.0")
    monkeypatch.setattr(health.shutil, "which", lambda name: "/bin/docker")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret-command", 10)
    monkeypatch.setattr(health.subprocess, "run", timeout)
    report = health.inspect_benchmark("jobbench", online=True)
    assert not report["infrastructure_available"]
    assert "secret-command" not in json.dumps(report)


def test_cli_benchmark_health_does_not_initialize_project(monkeypatch, tmp_path, capsys):
    from praxile.cli_commands.setup import cmd_doctor
    monkeypatch.setattr(health, "inspect_benchmark", lambda *args, **kwargs:
                        {"infrastructure_available": False, "execution_verified": False})
    assert cmd_doctor(argparse.Namespace(benchmark="workbuddy", online=False), tmp_path) == 1
    assert not list(tmp_path.iterdir())
    assert not json.loads(capsys.readouterr().out)["execution_verified"]
