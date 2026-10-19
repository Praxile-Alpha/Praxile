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
        return SimpleNamespace(returncode=0, stdout=b"29.8.0", stderr=b"")
    monkeypatch.setattr(health.subprocess, "run", run)
    report = health.inspect_benchmark("workbuddy", online=online)
    assert report["infrastructure_available"]
    assert not report["execution_verified"]
    assert len(calls) == (2 if online else 1)
    if online:
        assert calls[-1] == ["/bin/docker", "version", "--format", "{{.Server.Version}}"]


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
