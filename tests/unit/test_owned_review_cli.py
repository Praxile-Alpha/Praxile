import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.eval import owned_review_cli as cli


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = Config.load(tmp_path / "judge")
    config.data["model_roles"]["content_review_judge"] = {"provider": "judge", "model": "fixture"}
    config.data["model_providers"] = {"judge": {"type": "openai_compatible", "base_url": "http://127.0.0.1:1"}}
    monkeypatch.setattr(cli.Config, "load", lambda path: config)
    monkeypatch.setattr(cli, "load_task", lambda path: SimpleNamespace(fingerprint="sha256:" + "a" * 64))
    inputs = {"source_text": "PRIVATE_SOURCE", "output_text": "PRIVATE_OUTPUT", "requirements": [
        {"id": "r1", "requirement": "PRIVATE_REQUIREMENT", "source_quote": "PRIVATE_SOURCE"}]}
    binding = {key: "a" * 64 for key in ("task_fingerprint", "execution_sha256", "trajectory_sha256",
        "source_sha256", "output_sha256", "evaluator_sha256")}
    prepare = Mock(return_value=(inputs, binding))
    monkeypatch.setattr(cli, "prepare_owned_review", prepare)
    run = Mock(return_value={"status": "reviewed", "promotion_eligible": False})
    monkeypatch.setattr(cli, "run_content_review", run)
    args = ["--project", str(tmp_path / "judge"), "--task", str(tmp_path / "task.json"),
        "--execution-dir", str(tmp_path / "execution"), "--evaluator-root", str(tmp_path / "private"),
        "--source-path", "source.docx", "--output-path", "output.docx",
        "--source-format", "docx_body", "--output-format", "docx_body",
        "--run-id", "review", "--total-output-tokens", "100"]
    return config, args, prepare, run


def test_preflight_is_readonly_and_does_not_print_materials(setup, tmp_path, capsys):
    _, args, prepare, run = setup
    assert cli.main(args) == 0
    run.assert_not_called()
    output = capsys.readouterr().out
    assert "PRIVATE_" not in output and "127.0.0.1" not in output
    assert json.loads(output.splitlines()[0])["group_count"] == 1
    assert prepare.call_args.kwargs["source_format"] == "docx_body"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("public", [False, True])
def test_cloud_requires_separate_permission(setup, public):
    config, args, _, run = setup
    config.data["model_providers"]["judge"]["base_url"] = "https://judge.invalid/v1"
    assert cli.main([*args, "--execute", *(["--public"] if public else [])]) == (0 if public else 2)
    if public:
        assert run.call_args.kwargs["private"] is False
        assert run.call_args.kwargs["inputs"]["source_text"] == "PRIVATE_SOURCE"
    else:
        run.assert_not_called()


@pytest.mark.parametrize("flag,value", [("--run-id", "../escape"), ("--total-output-tokens", "0"),
    ("--total-output-tokens", "9000"), ("--batch-size", "0"), ("--timeout-per-call", "301")])
def test_invalid_plan_does_not_call_judge(setup, flag, value):
    _, args, _, run = setup
    assert cli.main([*args, flag, value, "--execute"]) == 2
    run.assert_not_called()


def test_judge_storage_inside_execution_rejected(setup, tmp_path):
    _, args, _, run = setup
    assert cli.main([*args, "--project", str(tmp_path / "execution/judge"), "--execute"]) == 2
    run.assert_not_called()


def test_exceptions_are_redacted_and_interruption_is_explicit(setup, capsys):
    _, args, _, run = setup
    run.side_effect = ValueError("SECRET response body")
    assert cli.main([*args, "--execute"]) == 2
    assert "SECRET" not in capsys.readouterr().err
    run.side_effect = KeyboardInterrupt()
    assert cli.main([*args, "--execute"]) == 130
