import hashlib
import subprocess

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.documents import read_document
from praxile.environment import FileSystemEnv
from praxile.security import SafetyPolicy
from praxile.tools import ToolRegistry

pytestmark = [pytest.mark.resource, pytest.mark.integration]


@pytest.fixture
def context(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    return config, SafetyPolicy(config)


def test_xlsx_rows_formula_and_sheet_selection(context, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "Budget"
    book.active.append(["Item", "Amount"])
    book.active.append(["Award", "=35000"])
    path = tmp_path / "budget.xlsx"
    book.save(path)
    book.close()
    result = read_document(*context, "budget.xlsx", start=2, limit=1, sheet="Budget")
    assert result.status == "success"
    assert "=35000" in result.data["units"][0]["text"]
    assert result.data["next_start"] is None
    assert result.data["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "no recalculation" in result.data["limitation"]


def test_docx_order_and_character_paging(context, tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("A" * 4000 + "tail")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "table"
    document.add_paragraph("last")
    document.save(tmp_path / "memo.docx")
    first = read_document(*context, "memo.docx", limit=1)
    assert first.status == "success"
    assert first.data["next_start"] == 2
    assert first.data["units"][0]["next_char_offset"] == 4000
    tail = read_document(*context, "memo.docx", limit=1, char_offset=4000)
    assert tail.data["units"][0]["text"] == "tail"
    rest = read_document(*context, "memo.docx", start=2)
    assert [u["text"] for u in rest.data["units"]] == ["table", "last"]


def test_binary_read_redirect_and_tool_dispatch(context, tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"%PDF invalid")
    assert FileSystemEnv(*context).read_file("broken.pdf").data["suggested_action"] == "read_document"
    tools = ToolRegistry(context[0])
    try:
        result = tools.execute({"type": "read_document", "path": "broken.pdf"}, task_id="fixture", step=1)
        assert result["status"] == "failure"
    finally:
        tools.close()


def test_invalid_options_paths_size_and_timeout(context, tmp_path, monkeypatch):
    assert read_document(*context, "../outside.pdf").status == "blocked"
    assert read_document(*context, "a.pdf", limit=True).status == "failure"
    assert read_document(*context, "a.pdf", limit=21).status == "failure"
    assert read_document(*context, "a.pdf", char_offset=-1).status == "failure"
    assert read_document(*context, "a.txt").status == "failure"
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 25)
    monkeypatch.setattr(subprocess, "run", timed_out)
    assert "timed out" in read_document(*context, "a.pdf").output


def test_real_pdf_page_text(context, tmp_path):
    pytest.importorskip("pdfplumber")
    stream = b"BT /F1 12 Tf 72 720 Td (Award memo) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(index).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n"
    raw += b"".join(f"{position:010} 00000 n \n".encode() for position in offsets[1:])
    raw += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (tmp_path / "award.pdf").write_bytes(raw)
    result = read_document(*context, "award.pdf")
    assert result.status == "success"
    assert result.data["unit_kind"] == "page"
    assert result.data["units"][0]["text"] == "Award memo"
    assert result.data["next_start"] is None
