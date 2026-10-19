from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2 import candidate_design_run as module
from praxile.eval.v2.schema import EvalSchemaError


@pytest.fixture
def setup_native(tmp_path, monkeypatch):
    source = tmp_path / "native.json"
    source.write_text(json.dumps({"schema_version": "praxile.native_benchmark_run.v1",
        "experience_mode": "fresh_disabled", "official_details": "PRIVATE_SENTINEL",
        "trajectory": {"task_id": "dev", "start_time": "2026-10-19T00:00:00Z",
            "actions": [{"step": 1, "action_type": "run_command",
                "input": {"command": "PRIVATE_SENTINEL"}, "status": "failure",
                "observation": {"data": {"exit_code": 1, "stdout": "PRIVATE_SENTINEL"}}}]}}))
    calls = []
    class Router:
        def chat(self, messages, **kwargs):
            assert "PRIVATE_SENTINEL" not in json.dumps(messages)
            calls.append(messages)
            return {"content": '{"decision":"abstain"}', "usage": {}, "finish_reason": "stop"}
    @contextmanager
    def router(*args, **kwargs):
        yield Router(), {"model": "fixture", "config_digest": "fixed"}
    monkeypatch.setattr(module, "configured_design_router", router)
    config = SimpleNamespace(paths=SimpleNamespace(state=tmp_path / "state"))
    options = dict(expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        track="development", design_id="native-design", development_task_ids=["dev"],
        heldout_task_ids=["heldout"], applies_to={"repositories": ["office"],
            "task_signals": ["spreadsheet"], "does_not_apply_when": ["no workbook"]},
        max_output_tokens=8000)
    return config, source, options, calls


def test_native_projection_uses_durable_designer_and_resumes_without_model(setup_native):
    config, source, options, calls = setup_native
    report = module.run_native_candidate_design(config, source, **options)
    root = config.paths.state / "eval/v2/candidate_designs/native-design"
    plan = json.loads((root / "manifest.json").read_text())
    assert plan["source_evidence"]["source_result_sha256"] == options["expected_sha256"]
    assert plan["source_evidence"]["projection_version"] == 2
    assert "PRIVATE_SENTINEL" not in json.dumps(plan)
    assert (root / "request-intent.json").exists()
    assert report["decision"] == "NO_ENTRY"
    assert module.run_native_candidate_design(config, source, **options, resume=True) == report
    assert len(calls) == 1


@pytest.mark.parametrize("change", [{"expected_sha256": "0" * 64}, {"track": "heldout"},
                                  {"heldout_task_ids": ["dev"]}])
def test_native_rejects_invalid_source_before_router(setup_native, monkeypatch, change):
    config, source, options, calls = setup_native
    monkeypatch.setattr(module, "configured_design_router", lambda *a, **k: pytest.fail("router accessed"))
    with pytest.raises(EvalSchemaError):
        module.run_native_candidate_design(config, source, **{**options, **change})
    assert calls == []
    assert not config.paths.state.exists()


def test_changed_source_cannot_resume_even_with_new_matching_hash(setup_native):
    config, source, options, calls = setup_native
    module.run_native_candidate_design(config, source, **options)
    source.write_text(source.read_text() + "\n")
    options["expected_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(EvalSchemaError):
        module.run_native_candidate_design(config, source, **options, resume=True)
    assert len(calls) == 1


def test_native_failed_generation_cannot_be_replayed(setup_native, monkeypatch):
    config, source, options, calls = setup_native
    def fail(**kwargs):
        calls.append("request")
        root = config.paths.state / "eval/v2/candidate_designs/native-design"
        assert (root / "request-intent.json").exists()
        raise TimeoutError("fixture request timeout")
    monkeypatch.setattr(module, "design_development_candidates", fail)
    with pytest.raises(TimeoutError):
        module.run_native_candidate_design(config, source, **options)
    with pytest.raises(EvalSchemaError, match="Resume cannot resend"):
        module.run_native_candidate_design(config, source, **options, resume=True)
    assert calls == ["request"]


def test_native_cli_dispatches_without_legacy_event_store(setup_native, monkeypatch, capsys):
    from praxile.cli_parser import build_parser
    from praxile.cli_commands import evals
    config, source, options, calls = setup_native
    scope = source.parent / "scope.json"
    scope.write_text(json.dumps(options["applies_to"]))
    monkeypatch.setattr(evals, "load", lambda root: (config, SimpleNamespace(initialize=lambda _: None)))
    monkeypatch.setattr(evals, "EventStore", lambda *a: pytest.fail("legacy EventStore opened"))
    args = build_parser().parse_args(["eval", "design-native-context", str(source),
        "--source-sha256", options["expected_sha256"], "--track", "development",
        "--design-id", "native-design", "--scope", str(scope),
        "--development-task-id", "dev", "--heldout-task-id", "heldout", "--json"])
    assert args.func(args, source.parent) == 0
    assert json.loads(capsys.readouterr().out)["experiments_started"] is False
    assert len(calls) == 1
