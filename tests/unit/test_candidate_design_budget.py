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
