"""Optional document parsers executed in an isolated, bounded-time child."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


MEDIA = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}


def inspect_document(path: Path, kind: str) -> dict:
    if kind not in MEDIA.values():
        return {"status": "unsupported", "reason": "unknown_document_format"}
    if kind != "pdf":
        from defusedxml.ElementTree import fromstring
        from defusedxml.common import DefusedXmlException
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > 50_000_000:
                return {"status": "failed", "reason": "expanded_package_limit"}
            if len({item.filename for item in members}) != len(members):
                return {"status": "failed", "reason": "duplicate_package_member"}
            if any(item.flag_bits & 1 for item in members):
                return {"status": "unsupported", "reason": "encrypted_package"}
            if any("vbaproject" in item.filename.lower() for item in members):
                return {"status": "unsupported", "reason": "macro_content"}
            for member in members:
                if member.filename.endswith((".xml", ".rels")):
                    payload = package.read(member)
                    try:
                        fromstring(payload, forbid_dtd=True, forbid_entities=True, forbid_external=True)
                    except DefusedXmlException:
                        return {"status": "failed", "reason": "xml_entity_declarations_forbidden"}
    if kind == "xlsx":
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            cells = 0
            for sheet in book:
                for row in sheet.iter_rows():
                    cells += len(row)
                    if cells > 1_000_000:
                        return {"status": "failed", "reason": "cell_limit"}
            details = {"sheets": book.sheetnames, "cells_visited": cells, "formulas_recalculated": False}
        finally:
            book.close()
    elif kind == "docx":
        from docx import Document
        document = Document(path)
        headings = [paragraph.text.strip() for paragraph in document.paragraphs
                    if paragraph.style is not None and paragraph.style.style_id in
                    {"Heading" + str(level) for level in range(1, 10)}]
        if len(headings) > 1000 or sum(map(len, headings)) > 100_000:
            return {"status": "failed", "reason": "heading_metadata_limit"}
        details = {"paragraphs": len(document.paragraphs), "tables": len(document.tables), "headings": headings}
    elif kind == "pptx":
        from pptx import Presentation
        document = Presentation(path)
        details = {"slides": len(document.slides)}
    else:
        from pypdf import PdfReader
        with path.open("rb") as source:
            document = PdfReader(source, strict=True)
            if document.is_encrypted:
                return {"status": "unsupported", "reason": "encrypted_pdf"}
            if len(document.pages) > 2000:
                return {"status": "failed", "reason": "page_limit"}
            details = {"pages": len(document.pages)}
    return {"status": "passed", "opened": True, "structure": details,
            "rendered": False, "semantic_quality_verified": False}


def main() -> None:
    # A resource ceiling supplements timeout on Linux. Other platforms should
    # run untrusted binary documents in the owning benchmark container.
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))
    try:
        result = inspect_document(Path(sys.argv[1]), sys.argv[2])
    except ImportError:
        result = {"status": "unsupported", "reason": "install_praxile_documents_extra"}
    except Exception as exc:
        result = {"status": "failed", "reason": "document_parse_failed", "error_type": type(exc).__name__}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
