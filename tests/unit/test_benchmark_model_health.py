import argparse
import json
from types import SimpleNamespace

import pytest

from praxile.config import Config
from praxile.services import benchmark_model_health as health


def configured(tmp_path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {"executor": {"type": "openai_compatible", "api_key_env": "FIXTURE_KEY"},
                                      "judge": {"type": "openai_compatible", "api_key_env": "FIXTURE_KEY"}}
    config.data["model_roles"]["coding_agent"] = {"provider": "executor", "model": "model-a"}
    config.data["model_roles"]["content_review_judge"] = {
        "provider": "judge", "model": "model-b", "request_options": {"thinking": {"type": "disabled"}}}
    return config


def test_offline_does_not_construct_router_or_initialize_state(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "ModelRouter", lambda *a: pytest.fail("offline must not instantiate providers"))
    monkeypatch.setenv("FIXTURE_KEY", "PRIVATE_KEY")
    report = health.inspect_model_roles(configured(tmp_path))
    assert {item["status"] for item in report["roles"]} == {"configured_unverified"}
    assert not report["models_reachable"]
    assert "PRIVATE_KEY" not in json.dumps(report)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("judge_outcome", ["success", "error", "invalid"])
def test_explicit_primary_probes_are_bounded_redacted_and_closed(tmp_path, monkeypatch, judge_outcome):
    calls, closed = [], []
    def provider(name):
        def chat(request):
            calls.append((name, request))
            if name == "judge" and judge_outcome == "error":
                raise RuntimeError("SECRET response")
            if name == "judge" and judge_outcome == "invalid":
                return {}
            return {"content": "OK"}
        return SimpleNamespace(chat=chat, transport=SimpleNamespace(close=lambda: closed.append(name)))
    monkeypatch.setattr(health, "ModelRouter", lambda *a: SimpleNamespace(
        providers={name: provider(name) for name in ("executor", "judge")}))
    report = health.inspect_model_roles(configured(tmp_path), probe=True)
    assert [name for name, _ in calls] == ["executor", "judge"]
    assert sorted(closed) == ["executor", "judge"]
    assert report["models_reachable"] is (judge_outcome == "success")
    assert not report["official_judge_verified"]
    assert "SECRET" not in json.dumps(report)
    for _, request in calls:
        assert request["timeout"] == 8 and request["max_tokens"] == 32
        assert request["messages"] == [{"role": "user", "content": "Reply with OK only."}]
    assert calls[-1][1]["request_options"] == {"thinking": {"type": "disabled"}}


def test_missing_explicit_judge_does_not_fall_back_to_executor(tmp_path, monkeypatch):
    config = configured(tmp_path)
    config.data["model_roles"].pop("content_review_judge")
    monkeypatch.setattr(health, "ModelRouter", lambda *a: SimpleNamespace(providers={}))
    report = health.inspect_model_roles(config, probe=True)
    assert report["roles"][-1]["status"] == "not_configured"
    assert not report["roles"][-1]["probe_attempted"]


def test_cli_probe_requires_benchmark(tmp_path):
    from praxile.cli_commands.setup import cmd_doctor
    with pytest.raises(ValueError, match="requires --benchmark"):
        cmd_doctor(argparse.Namespace(probe_benchmark_models=True, benchmark=None), tmp_path)
    assert not list(tmp_path.iterdir())


def test_cli_requested_probe_failure_changes_exit_code(tmp_path, monkeypatch, capsys):
    from praxile.cli_commands.setup import cmd_doctor
    monkeypatch.setattr("praxile.services.benchmark_health.inspect_benchmark",
                        lambda *a, **k: {"infrastructure_available": True,
                            "checks": [{"name": "model_endpoint", "status": "not_checked"}]})
    probes = []
    def inspect(config, *, probe):
        probes.append(probe)
        return {"models_reachable": False}
    monkeypatch.setattr(health, "inspect_model_roles", inspect)
    assert cmd_doctor(argparse.Namespace(benchmark="jobbench", online=False,
        probe_benchmark_models=True), tmp_path) == 1
    assert probes == [True]
    report = json.loads(capsys.readouterr().out)
    assert not report["model_health"]["models_reachable"]
    assert report["checks"][0]["status"] == "see_model_health"
    assert not list(tmp_path.iterdir())
