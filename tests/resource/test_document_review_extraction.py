import io
import zipfile

import pytest

from praxile.eval.document_review_extraction import extract_docx_body
from praxile.eval.content_review_run import validate_execution_binding

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_real_parser_preserves_long_blocks_and_table_order():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    long_text = "x" * 5000
    document.add_paragraph(long_text)
    document.add_table(rows=1, cols=1).cell(0, 0).text = "middle table"
    document.add_paragraph("last paragraph")
    raw = io.BytesIO()
    document.save(raw)
    text, evidence = extract_docx_body(raw.getvalue())
    assert text == long_text + "\nmiddle table\nlast paragraph"
    binding = {key: "a" * 64 for key in ("task_fingerprint", "execution_sha256", "trajectory_sha256",
        "source_sha256", "output_sha256", "evaluator_sha256")}
    binding["output_extraction"] = evidence
    validate_execution_binding(binding, output_text=text)
    with pytest.raises(ValueError, match="extraction binding"):
        validate_execution_binding(binding, output_text="changed")


def test_real_parser_rejects_document_over_text_budget():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("x" * 200001)
    raw = io.BytesIO()
    document.save(raw)
    with pytest.raises(ValueError, match="extraction unavailable"):
        extract_docx_body(raw.getvalue())


def test_invalid_package_is_not_treated_as_empty_document():
    with pytest.raises(ValueError):
        extract_docx_body(b"not a docx")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("large", b"x" * (64 * 1024**2 + 1))
    with pytest.raises(ValueError):
        extract_docx_body(raw.getvalue())
