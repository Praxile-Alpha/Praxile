import pytest

from praxile.artifact_inventory import inspect_outputs

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_inventory_rejects_fake_docx_and_empty_files(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "fake.docx").write_text("text")
    (output / "empty.docx").touch()
    report = inspect_outputs(tmp_path, "output")
    assert report["complete"]
    assert report["status"] == "issues"
    assert all(item["status"] in {"failed", "unsupported"} for item in report["files"])
    assert not report["quality_verified"]
    assert not report["required_outputs_verified"]
    assert report["official_evaluation"] == "not_run"


def test_real_docx_is_structurally_readable_not_quality_verified(tmp_path):
    docx = pytest.importorskip("docx")
    output = tmp_path / "output"
    output.mkdir()
    document = docx.Document()
    document.add_paragraph("Example")
    document.save(output / "memo.docx")
    report = inspect_outputs(tmp_path, "output")
    assert report["status"] == "clean_structure"
    assert report["files"][0]["sha256"]
    assert not report["quality_verified"]


def test_missing_empty_unsupported_and_escape(tmp_path):
    assert inspect_outputs(tmp_path, "missing")["status"] == "missing"
    assert inspect_outputs(tmp_path, "../outside")["status"] == "blocked"
    output = tmp_path / "output"
    output.mkdir()
    assert inspect_outputs(tmp_path, "output")["status"] == "empty"
    (output / "link").symlink_to(tmp_path)
    (output / "a.unknown").write_bytes(b"something")
    report = inspect_outputs(tmp_path, "output")
    assert report["status"] == "issues"
    assert {item["status"] for item in report["files"]} == {"failed", "unsupported"}


def test_scan_bound_and_strict_json(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "bad.json").write_text('{"x":NaN}')
    assert inspect_outputs(tmp_path, "output")["files"][0]["status"] == "failed"
    for index in range(201):
        (output / f"{index}.txt").write_text("ok")
    report = inspect_outputs(tmp_path, "output")
    assert report["status"] == "incomplete"
    assert not report["complete"]
    assert len(report["files"]) == 200
