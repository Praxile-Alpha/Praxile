import pytest

from praxile.capability_probe import probe

pytestmark = pytest.mark.resource


def test_real_xlsx_capability_roundtrip():
    pytest.importorskip("openpyxl")
    pytest.importorskip("defusedxml")
    report = probe("xlsx_values.v1")
    assert report["status"] == "passed"
    assert report["checks"] == ["create", "read", "opener", "literal_roundtrip"]
    assert set(report["packages"]) == {"openpyxl", "et-xmlfile", "defusedxml"}
    assert report["model_called"] is False
    assert report["formulas_recalculated"] is False


def test_opener_failure_does_not_count_as_capability(monkeypatch):
    pytest.importorskip("openpyxl")
    monkeypatch.setattr("praxile.artifact_openers.inspect_document", lambda *args: {"status": "unsupported"})
    report = probe("xlsx_values.v1")
    assert report["status"] == "failed" and report["error_type"] == "RuntimeError"
    assert "checks" not in report


def test_unknown_profile_refused():
    with pytest.raises(ValueError):
        probe("unversioned")


def test_real_docx_capability_roundtrip():
    pytest.importorskip("docx")
    pytest.importorskip("defusedxml")
    report = probe("docx_text.v1")
    assert report["status"] == "passed"
    assert report["checks"] == ["create", "read", "opener", "heading_table_roundtrip"]
    assert set(report["packages"]) == {"python-docx", "lxml", "defusedxml"}
    assert report["model_called"] is False and report["visual_quality_verified"] is False


def test_docx_opener_failure_is_not_passed(monkeypatch):
    pytest.importorskip("docx")
    monkeypatch.setattr("praxile.artifact_openers.inspect_document", lambda *args: {"status": "failed"})
    assert probe("docx_text.v1")["status"] == "failed"
