import subprocess
import zipfile

import pytest

pytest.importorskip("defusedxml")

from praxile.deliverables import verify_deliverables
from praxile.artifact_openers import MEDIA, inspect_document

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.timeout(45)]


def request(kind):
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request",
            "outputs": [{"path": "output." + kind,
                         "media_type": next(media for media, ext in MEDIA.items() if ext == kind)}]}


@pytest.mark.parametrize("kind,module", [("xlsx", "openpyxl"), ("docx", "docx"),
                                         ("pptx", "pptx"), ("pdf", "pypdf")])
def test_actual_document_roundtrip(tmp_path, kind, module):
    library = pytest.importorskip(module)
    path = tmp_path / ("output." + kind)
    if kind == "xlsx":
        book = library.Workbook()
        book.active.append(["count", "formula"])
        book.active.append([2, "=A2*2"])
        book.save(path)
        book.close()
    elif kind == "docx":
        book = library.Document()
        book.add_paragraph("Public request deliverable")
        book.save(path)
    elif kind == "pptx":
        book = library.Presentation()
        book.slides.add_slide(book.slide_layouts[0])
        book.save(path)
    else:
        book = library.PdfWriter()
        book.add_blank_page(width=200, height=200)
        book.write(path)
        book.close()
    result = verify_deliverables(tmp_path, request(kind))
    assert result["passed"]
    output = result["outputs"][0]
    assert output["opened"]
    assert not output["rendered"]
    if kind == "xlsx":
        assert output["structure"]["cells_visited"] == 4
        assert not output["structure"]["formulas_recalculated"]


def test_invalid_document_and_timeout(tmp_path, monkeypatch):
    (tmp_path / "output.xlsx").write_bytes(b"not a spreadsheet")
    assert not verify_deliverables(tmp_path, request("xlsx"))["passed"]
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("parser", 15)
    monkeypatch.setattr("praxile.deliverables.subprocess.run", timeout)
    result = verify_deliverables(tmp_path, request("xlsx"))
    assert result["outputs"][0]["reason"] == "document_parser_timeout"


def test_macro_package_is_not_opened(tmp_path):
    path = tmp_path / "macro.xlsx"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("xl/vbaProject.bin", b"macro")
    assert inspect_document(path, "xlsx") == {"status": "unsupported", "reason": "macro_content"}


def test_expanded_archive_limit_before_parsing(tmp_path):
    path = tmp_path / "many.xlsx"
    with zipfile.ZipFile(path, "w") as package:
        for index in range(2001):
            package.writestr(str(index), b"")
    assert inspect_document(path, "xlsx")["reason"] == "expanded_package_limit"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_entity_declarations_are_rejected(tmp_path, encoding):
    path = tmp_path / "entity.docx"
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'.encode(encoding))
    assert inspect_document(path, "docx")["reason"] == "xml_entity_declarations_forbidden"
