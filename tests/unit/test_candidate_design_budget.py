from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from praxile.eval.v2 import candidate_design_run as module
from praxile.eval.v2.schema import EvalSchemaError


@pytest.fixture
def setup_run(tmp_path, monkeypatch):
    calls = []
    @contextmanager
    def router(*args, **kwargs):
        yield object(), {"model": "fixture", "config_digest": "fixed"}
    monkeypatch.setattr(module, "configured_design_router", router)
    monkeypatch.setattr(module, "collect_development_evidence", lambda *args, **kwargs: {"evidence": [{"fixture": True}]})
    def generate(**kwargs):
        calls.append(kwargs["max_output_tokens"])
        return {"decision": "NO_ENTRY", "candidates": [], "composition": None, "promotion_eligible": False}
    monkeypatch.setattr(module, "design_development_candidates", generate)
    config = SimpleNamespace(paths=SimpleNamespace(state=tmp_path))
    kwargs = dict(design_id="budget-design", experiment_id="dev-experiment", arm="baseline",
                  development_task_ids=["dev"], heldout_task_ids=["heldout"], applies_to={})
    return config, kwargs, calls, tmp_path / "eval/v2/candidate_designs/budget-design"


def test_explicit_budget_frozen_and_resume_does_not_repeat_call(setup_run):
    config, kwargs, calls, root = setup_run
    module.run_candidate_design(config, None, **kwargs, max_output_tokens=8000)
    raw = (root / "manifest.json").read_bytes()
    plan = json.loads(raw)
    assert plan["schema_version"] == "praxile.candidate_design_run.v2"
    assert plan["generation_budget"] == {"max_output_tokens": 8000, "timeout_seconds": 60}
    module.run_candidate_design(config, None, **kwargs, max_output_tokens=8000, resume=True)
    assert calls == [8000]
    for change in (4000, None, 16000):
        with pytest.raises(EvalSchemaError):
            module.run_candidate_design(config, None, **kwargs, max_output_tokens=change, resume=True)
    assert calls == [8000] and (root / "manifest.json").read_bytes() == raw


def test_implicit_legacy_budget_preserves_manifest(setup_run):
    config, kwargs, calls, root = setup_run
    module.run_candidate_design(config, None, **kwargs)
    raw = (root / "manifest.json").read_bytes()
    assert json.loads(raw)["schema_version"] == "praxile.candidate_design_run.v1"
    assert "generation_budget" not in json.loads(raw)
    module.run_candidate_design(config, None, **kwargs, resume=True)
    assert calls == [4000] and (root / "manifest.json").read_bytes() == raw


@pytest.mark.parametrize("value", [True, 255, 16385, "8000"])
def test_invalid_budget_rejected_without_source_or_model(setup_run, monkeypatch, value):
    config, kwargs, calls, root = setup_run
    monkeypatch.setattr(module, "collect_development_evidence", lambda *a, **k: pytest.fail("source accessed"))
    with pytest.raises(EvalSchemaError, match="budget"):
        module.run_candidate_design(config, None, **kwargs, max_output_tokens=value)
    assert not root.exists() and calls == []


def test_cli_parses_explicit_budget():
    from praxile.cli_parser import build_parser
    args = build_parser().parse_args(["eval", "design-context", "dev", "--design-id", "draft",
        "--scope", "scope.json", "--development-task-id", "d", "--heldout-task-id", "h",
        "--max-output-tokens", "8000"])
    assert args.max_output_tokens == 8000


def test_intent_persisted_before_generator_and_failed_run_not_replayed(setup_run, monkeypatch):
    config, kwargs, calls, root = setup_run
    def fail(**unused):
        calls.append("requested")
        intent = json.loads((root / "request-intent.json").read_text())
        trajectory = json.loads((root / "trajectory.json").read_text())
        assert intent["request_dispatched"] is None
        assert intent["remote_usage_known"] is False
        assert trajectory["candidate_design_request_intent"] == intent
        raise TimeoutError("fixture timeout")
    monkeypatch.setattr(module, "design_development_candidates", fail)
    with pytest.raises(TimeoutError):
        module.run_candidate_design(config, None, **kwargs)
    with pytest.raises(EvalSchemaError, match="new design ID"):
        module.run_candidate_design(config, None, **kwargs, resume=True)
    assert calls == ["requested"]
    assert not (root / "generated.json").exists()


def test_legacy_incomplete_manifest_cannot_trigger_paid_replay(setup_run):
    config, kwargs, calls, root = setup_run
    module.run_candidate_design(config, None, **kwargs)
    (root / "generated.json").unlink()
    (root / "request-intent.json").unlink()
    with pytest.raises(EvalSchemaError, match="prior request outcome is unknown"):
        module.run_candidate_design(config, None, **kwargs, resume=True)
    assert calls == [4000]


def test_empty_evidence_resume_does_not_need_request_intent(setup_run, monkeypatch):
    config, kwargs, calls, root = setup_run
    monkeypatch.setattr(module, "collect_development_evidence", lambda *a, **k: {"evidence": []})
    module.run_candidate_design(config, None, **kwargs)
    (root / "generated.json").unlink()
    report = module.run_candidate_design(config, None, **kwargs, resume=True)
    assert report["decision"] == "NO_ENTRY"
    assert not (root / "request-intent.json").exists()
    assert calls == []


def test_checkpoint_write_failure_prevents_request(setup_run, monkeypatch):
    config, kwargs, calls, root = setup_run
    original = module.write_json
    def write(path, data):
        if path.name == "trajectory.json":
            raise OSError("fixture disk failure")
        return original(path, data)
    monkeypatch.setattr(module, "write_json", write)
    with pytest.raises(OSError, match="disk failure"):
        module.run_candidate_design(config, None, **kwargs)
    assert calls == []
    assert (root / "request-intent.json").exists()


def test_explicit_request_timeout_is_forwarded_frozen_and_not_changed_on_resume(setup_run, monkeypatch):
    config, kwargs, calls, root = setup_run
    def generate(**options):
        calls.append(options["request_timeout_seconds"])
        return {"decision": "NO_ENTRY", "candidates": [], "composition": None, "promotion_eligible": False}
    monkeypatch.setattr(module, "design_development_candidates", generate)
    module.run_candidate_design(config, None, **kwargs, request_timeout_seconds=120)
    assert json.loads((root / "manifest.json").read_text())["generation_budget"] == {
        "max_output_tokens": 4000, "timeout_seconds": 120}
    module.run_candidate_design(config, None, **kwargs, request_timeout_seconds=120, resume=True)
    for value in (None, 60, 180):
        with pytest.raises(EvalSchemaError):
            module.run_candidate_design(config, None, **kwargs, request_timeout_seconds=value, resume=True)
    assert calls == [120]


@pytest.mark.parametrize("value", [True, 0, 301, 1.5, "120"])
def test_invalid_timeout_rejected_before_evidence(setup_run, monkeypatch, value):
    config, kwargs, calls, root = setup_run
    monkeypatch.setattr(module, "collect_development_evidence", lambda *a, **k: pytest.fail("source accessed"))
    with pytest.raises(EvalSchemaError, match="request timeout"):
        module.run_candidate_design(config, None, **kwargs, request_timeout_seconds=value)
    assert calls == [] and not root.exists()
