import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.eval.content_review_files import compare_review_runs, load_review_run
from praxile.eval.content_review_run import run_content_review
from praxile.utils import write_json


def generate(tmp_path, monkeypatch):
    config = Config.load(tmp_path)
    config.data["model_roles"]["content_review_judge"] = {"provider": "test", "model": "test"}
    router = Mock()
    router.providers = {"test": SimpleNamespace(transport=SimpleNamespace(close=Mock()))}
    router.describe_route.return_value = {"provider": "test", "model": "test"}
    def chat(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        return {"content": json.dumps({"items": [{"id": "r", "verdict": "preserved",
            "output_refs": [data["output_units"][0]["id"]], "reason": "Present"}]}), "usage": {}}
    router.chat.side_effect = chat
    monkeypatch.setattr("praxile.eval.content_review_run.ModelRouter", Mock(return_value=router))
    inputs = {"source_text": "Approval required", "output_text": "Approval required",
        "requirements": [{"id": "r", "requirement": "Keep approval", "source_quote": "Approval required"}]}
    for name in ("baseline", "candidate"):
        run_content_review(config, run_id=name, inputs=inputs, total_output_tokens=100, private=False)
    root = config.paths.state / "eval/v2/content_reviews"
    return root / "baseline", root / "candidate", router


def test_persisted_runs_compare_without_new_model_calls(tmp_path, monkeypatch):
    left, right, router = generate(tmp_path, monkeypatch)
    result = compare_review_runs(left, right)
    assert result["status"] == "compared"
    assert result["local_file_consistency_checked"] is True
    assert result["provenance_verified"] is False
    assert result["promotion_eligible"] is False
    assert router.chat.call_count == 2


@pytest.mark.parametrize("file", ["manifest.json", "inputs.json", "report.json", "trajectory.json"])
def test_changed_evidence_is_rejected(tmp_path, monkeypatch, file):
    left, _, _ = generate(tmp_path, monkeypatch)
    value = json.loads((left / file).read_text())
    if file == "manifest.json":
        value["judge_identity"]["model"] = "changed"
    elif file == "inputs.json":
        value["output_text"] = "changed"
    elif file == "report.json":
        value["plan"]["output_text_sha256"] = "b" * 64
    else:
        value["content_review_batches"] = []
    write_json(left / file, value)
    with pytest.raises(ValueError):
        load_review_run(left)


def test_copied_fabricated_span_rejected_even_if_report_and_trace_agree(tmp_path, monkeypatch):
    left, _, _ = generate(tmp_path, monkeypatch)
    report = json.loads((left / "report.json").read_text())
    for item in (report["items"][0], report["groups"][0]["items"][0]):
        item["output_spans"][0]["text"] = "Fabricated citation"
    trace = json.loads((left / "trajectory.json").read_text())
    trace["content_review_batches"] = [report]
    trace["content_reviews"] = report["groups"]
    write_json(left / "report.json", report)
    write_json(left / "trajectory.json", trace)
    with pytest.raises(ValueError, match="span"):
        load_review_run(left)


def test_linked_evidence_is_not_followed(tmp_path, monkeypatch):
    left, right, _ = generate(tmp_path, monkeypatch)
    (left / "report.json").unlink()
    (left / "report.json").symlink_to(right / "report.json")
    with pytest.raises(ValueError, match="regular"):
        load_review_run(left)


def test_cli_uses_persisted_runs_without_calling_model(tmp_path, monkeypatch, capsys):
    from praxile.eval.content_review_files import main
    left, right, router = generate(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["content_review_files", "--baseline", str(left), "--candidate", str(right)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "compared"
    assert router.chat.call_count == 2


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '[]'])
def test_evidence_decoder_is_strict(tmp_path, raw):
    from praxile.eval.content_review_files import _read
    (tmp_path / "test.json").write_text(raw)
    with pytest.raises(ValueError):
        _read(tmp_path, "test.json")
