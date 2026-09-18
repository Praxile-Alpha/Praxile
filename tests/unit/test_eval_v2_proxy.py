from __future__ import annotations

import json

import pytest

from praxile.eval.v2 import EvalSchemaError, ProxyEvalProposal, ProxyEvalRegistry
from praxile.cli_parser import build_parser
from praxile.config import Config
from praxile.utils import write_json


def _value() -> dict:
    return {
        "schema_version": "praxile.proxy_eval.v1",
        "proxy_id": "focused-efficiency",
        "version": "1",
        "hypothesis_id": "focused-investigation",
        "task_ids": ["dev-task"],
        "rationale": "Focused exploration should not increase token or tool cost.",
        "checks": [
            {"metric": "token_delta", "operator": "<=", "threshold": 0},
            {"metric": "tool_call_delta", "operator": "<=", "threshold": 0},
        ],
    }


def test_proxy_requires_approval_and_is_immutable(tmp_path) -> None:
    registry = ProxyEvalRegistry(tmp_path)
    proposal = ProxyEvalProposal.from_dict(_value())
    path = registry.propose(proposal)
    assert registry.propose(proposal) == path
    with pytest.raises(EvalSchemaError, match="human approval"):
        registry.load_approved(proposal.proxy_id, proposal.version)
    approval = registry.approve(proposal.proxy_id, proposal.version, reviewer="human")
    assert approval["proposal_digest"] == proposal.digest
    assert registry.load_approved(proposal.proxy_id, proposal.version)[0] == proposal
    altered = _value()
    altered["checks"][0]["threshold"] = -100
    with pytest.raises(EvalSchemaError, match="immutable"):
        registry.propose(ProxyEvalProposal.from_dict(altered))


def test_proxy_scores_only_selected_development_rows() -> None:
    proposal = ProxyEvalProposal.from_dict(_value())
    result = proposal.evaluate([
        {"task_id": "dev-task", "token_delta": -20, "tool_call_delta": -2},
        {"task_id": "heldout-task", "token_delta": 1000, "tool_call_delta": 1000},
    ])
    assert result["status"] == "passed"
    assert result["objective_claim"] is False
    assert result["checks"][0]["observed"] == -20
    unknown = proposal.evaluate([{"task_id": "dev-task", "token_delta": None, "tool_call_delta": -2}])
    assert unknown["status"] == "unknown"


def test_proxy_rejects_arbitrary_code_and_unsafe_thresholds() -> None:
    value = _value()
    value["checks"][0]["script"] = "import os"
    with pytest.raises(EvalSchemaError, match="requires metric"):
        ProxyEvalProposal.from_dict(value)
    value = _value()
    value["checks"][0]["threshold"] = float("nan")
    with pytest.raises(EvalSchemaError, match="finite"):
        ProxyEvalProposal.from_dict(value)


def test_cli_proxy_review_and_approval_are_explicit(tmp_path, monkeypatch, capsys) -> None:
    from praxile.cli_commands import evals as commands

    spec = tmp_path / "proxy.json"
    spec.write_text(json.dumps(_value()), encoding="utf-8")
    parser = build_parser()
    proposed = parser.parse_args(["eval", "proxy-propose", str(spec)])
    assert proposed.func(proposed, tmp_path) == 0
    review = parser.parse_args(["eval", "proxy-review", "focused-efficiency", "--version", "1"])
    assert review.func(review, tmp_path) == 0
    monkeypatch.setattr(commands, "safe_input", lambda prompt: "NO")
    approve = parser.parse_args(["eval", "proxy-approve", "focused-efficiency", "--version", "1", "--reviewer", "human"])
    with pytest.raises(ValueError, match="cancelled"):
        approve.func(approve, tmp_path)
    monkeypatch.setattr(commands, "safe_input", lambda prompt: "APPROVE focused-efficiency@1")
    assert approve.func(approve, tmp_path) == 0
    assert "Approved proxy eval" in capsys.readouterr().out


def test_proxy_run_reads_only_development_track(tmp_path, capsys) -> None:
    config = Config.load(tmp_path)
    registry = ProxyEvalRegistry(config.paths.state)
    proposal = ProxyEvalProposal.from_dict(_value())
    registry.propose(proposal)
    registry.approve(proposal.proxy_id, proposal.version, reviewer="human")
    experiment_root = config.paths.state / "eval" / "v2" / "experiments" / "dev-run"
    write_json(experiment_root / "manifest.json", {"track": "development"})
    write_json(experiment_root / "report.json", {"comparison": {"task_results": [
        {"task_id": "dev-task", "token_delta": -5, "tool_call_delta": -1},
        {"task_id": "heldout-task", "token_delta": 999, "tool_call_delta": 999},
    ]}})
    args = build_parser().parse_args([
        "eval", "proxy-run", "focused-efficiency", "--version", "1",
        "--experiment-id", "dev-run", "--json",
    ])
    assert args.func(args, tmp_path) == 0
    output = capsys.readouterr().out
    assert '"status": "passed"' in output
    assert "heldout-task" not in output
    write_json(experiment_root / "manifest.json", {"track": "clean"})
    with pytest.raises(ValueError, match="development-only"):
        args.func(args, tmp_path)
