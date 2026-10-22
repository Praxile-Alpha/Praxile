import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.eval.content_review_run import run_content_review
from praxile.model import ModelUnavailable


def setup(tmp_path, monkeypatch, failure=False):
    config = Config.load(tmp_path)
    config.data["model_roles"]["content_review_judge"] = {"provider": "fixture", "model": "m"}
    config.data["model_providers"] = {"fixture": {"type": "openai_compatible", "base_url": "https://example.invalid"}}
    router = Mock()
    close = Mock()
    router.providers = {"fixture": SimpleNamespace(transport=SimpleNamespace(close=close))}
    router.describe_route.return_value = {"provider": "fixture", "model": "m"}
    def chat(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        return {"content": json.dumps({"items": [{"id": "r", "verdict": "uncertain", "output_refs": [],
            "reason": "Insufficient evidence"}]}), "usage": {"prompt_tokens": 5, "completion_tokens": 10}}
    router.chat.side_effect = ModelUnavailable("sensitive error") if failure else chat
    factory = Mock(return_value=router)
    monkeypatch.setattr("praxile.eval.content_review_run.ModelRouter", factory)
    inputs = {"source_text": "source", "output_text": "output", "requirements": [
        {"id": "r", "requirement": "retain source", "source_quote": "source"}]}
    return config, inputs, router, factory, close


def test_durable_run_and_duplicate_id_refuses_replay(tmp_path, monkeypatch):
    config, inputs, router, factory, close = setup(tmp_path, monkeypatch)
    result = run_content_review(config, run_id="one", inputs=inputs, total_output_tokens=100, private=False)
    path = config.paths.state / "eval/v2/content_reviews/one"
    assert result["status"] == "reviewed"
    assert json.loads((path / "inputs.json").read_text()) == inputs
    assert (path / "trajectory.json").exists()
    assert (path.stat().st_mode & 0o777) == 0o700
    assert not list(path.glob("response-*.json"))
    close.assert_called_once()
    with pytest.raises(FileExistsError):
        run_content_review(config, run_id="one", inputs=inputs, total_output_tokens=100, private=False)
    factory.assert_called_once()
    router.chat.assert_called_once()


def test_failed_request_is_retained_without_provider_error_body(tmp_path, monkeypatch):
    config, inputs, router, _, close = setup(tmp_path, monkeypatch, failure=True)
    result = run_content_review(config, run_id="failed", inputs=inputs, total_output_tokens=100, private=False)
    path = config.paths.state / "eval/v2/content_reviews/failed"
    assert result["status"] == "abstained"
    assert "sensitive error" not in (path / "report.json").read_text()
    router.chat.assert_called_once()
    close.assert_called_once()


def test_default_private_mode_blocks_cloud_before_request(tmp_path, monkeypatch):
    config, inputs, router, _, close = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="loopback"):
        run_content_review(config, run_id="private", inputs=inputs, total_output_tokens=100)
    router.chat.assert_not_called()
    close.assert_called_once()


def test_explicit_retention_is_private_and_bound(tmp_path, monkeypatch):
    config, inputs, _, _, _ = setup(tmp_path, monkeypatch)
    run_content_review(config, run_id="retain", inputs=inputs, total_output_tokens=100,
        private=False, retain_responses=True)
    path = config.paths.state / "eval/v2/content_reviews/retain"
    checkpoint = json.loads((path / "response-0.json").read_text())
    report = json.loads((path / "report.json").read_text())
    assert checkpoint["batch_plan_sha256"] == report["plan_sha256"]


def test_symlink_state_and_unsafe_ids_refused(tmp_path, monkeypatch):
    config, inputs, router, _, _ = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        run_content_review(config, run_id="../escape", inputs=inputs, total_output_tokens=100)
    outside = tmp_path / "outside"
    outside.mkdir()
    config.paths.state.mkdir(exist_ok=True)
    (config.paths.state / "eval").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run_content_review(config, run_id="linked", inputs=inputs, total_output_tokens=100)
    assert list(outside.iterdir()) == []
    router.chat.assert_not_called()


def test_interrupt_closes_transport_and_leaves_nonreplayable_run(tmp_path, monkeypatch):
    config, inputs, router, _, close = setup(tmp_path, monkeypatch)
    router.chat.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_content_review(config, run_id="interrupted", inputs=inputs, total_output_tokens=100, private=False)
    path = config.paths.state / "eval/v2/content_reviews/interrupted"
    report = json.loads((path / "report.json").read_text())
    assert report["status"] == "interrupted_or_failed"
    assert report["automatic_replay_allowed"] is False
    assert (path / "trajectory.json").exists()
    close.assert_called_once()
    with pytest.raises(FileExistsError):
        run_content_review(config, run_id="interrupted", inputs=inputs, total_output_tokens=100, private=False)
    router.chat.assert_called_once()


def test_second_group_interrupt_keeps_first_group_usage_and_predispatch_snapshot(tmp_path, monkeypatch):
    config, inputs, router, _, close = setup(tmp_path, monkeypatch)
    inputs["requirements"].append({"id": "second", "requirement": "retain source", "source_quote": "source"})
    path = config.paths.state / "eval/v2/content_reviews/partial"
    original = router.chat.side_effect
    seen = []
    def chat(*args, **kwargs):
        report = json.loads((path / "report.json").read_text())
        seen.append(report)
        assert report["pending_group_index"] == len(seen) - 1
        assert report["automatic_replay_allowed"] is False
        if len(seen) == 2:
            raise KeyboardInterrupt()
        return original(*args, **kwargs)
    router.chat.side_effect = chat
    with pytest.raises(KeyboardInterrupt):
        run_content_review(config, run_id="partial", inputs=inputs, total_output_tokens=100,
                           batch_size=1, private=False)
    report = json.loads((path / "report.json").read_text())
    assert report["status"] == "interrupted_or_failed"
    assert report["pending_group_index"] == 1
    assert report["unreviewed_ids"] == ["second"]
    assert len(report["groups"]) == 1
    evidence = report["usage_evidence"]
    assert evidence["calls"] == 2 and evidence["recorded_calls"] == 1
    assert evidence["known_tokens"] == {"prompt_tokens": 5, "completion_tokens": 10}
    assert evidence["complete_tokens"] == {"prompt_tokens": None, "completion_tokens": None}
    assert evidence["cost_usd"] is None
    assert evidence["transport_retry_usage_verified"] is False
    close.assert_called_once()
