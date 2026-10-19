import hashlib
import json
from types import SimpleNamespace

import pytest

from praxile.deliverables import validate_contract, verify_deliverables
from praxile.eval.native_worker import benchmark_config


def contract(limit=1):
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "checklist.docx", "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "structure": {"max_pages": limit}}]}


@pytest.mark.parametrize("limit", [0, True, 2001, "1"])
def test_invalid_page_contract(limit):
    with pytest.raises(ValueError, match="max_pages"):
        validate_contract(contract(limit))


@pytest.mark.parametrize("mode", ["missing", "stale", "over", "passed"])
def test_layout_evidence_is_required_and_bound_to_same_bytes(tmp_path, monkeypatch, mode):
    (tmp_path / "checklist.docx").write_bytes(b"fixture")
    monkeypatch.setattr("praxile.deliverables.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0,
        stdout=json.dumps({"status": "passed", "structure": {"tables": 1}, "rendered": False}).encode()))
    def checker(path, limit):
        assert path == "checklist.docx" and limit == 1
        return {"status": "failure" if mode == "over" else "success", "data": {
            "source_sha256": "wrong" if mode == "stale" else hashlib.sha256(b"fixture").hexdigest(),
            "pages": 5 if mode == "over" else 1, "page_limit_met": mode != "over"}}
    report = verify_deliverables(tmp_path, contract(), layout_checker=None if mode == "missing" else checker)
    assert report["passed"] is (mode == "passed")
    if mode == "passed":
        assert report["outputs"][0]["structure_checks"][0]["actual"] == 1
    assert report["semantic_quality_verified"] is False


def test_native_renderer_setting_is_explicit(tmp_path):
    config = benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=1,
                              renderer_executable="/usr/bin/soffice")
    assert config.get("documents", "renderer_executable") == "/usr/bin/soffice"
    with pytest.raises(ValueError, match="renderer_executable"):
        benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=1,
                         renderer_executable="soffice")
