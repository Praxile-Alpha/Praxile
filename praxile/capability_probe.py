"""Local functional capability probes; no model, network or task data."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import tempfile

PROFILE_PACKAGES = {
    "xlsx_values.v1": ("openpyxl", "et-xmlfile", "defusedxml"),
    "docx_text.v1": ("python-docx", "lxml", "defusedxml"),
    "pdf_text.v1": ("pypdf", "pdfplumber", "pdfminer.six"),
}
PROFILE_CHECKS = {
    "xlsx_values.v1": ["create", "read", "opener", "literal_roundtrip"],
    "docx_text.v1": ["create", "read", "opener", "heading_table_roundtrip"],
    "pdf_text.v1": ["read_pages", "opener", "review_extraction", "text_digest"],
}
PROFILES = tuple(PROFILE_PACKAGES)


def _pdf_probe():
    import hashlib
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    from .config import Config, ProjectPaths, default_config
    from .security import SafetyPolicy
    from .documents import read_document
    from .artifact_openers import inspect_document
    from .eval.document_review_extraction import extract_pdf_text

    texts = ["First probe page", "Second probe page"]
    with tempfile.TemporaryDirectory(prefix="praxile-capability-") as directory:
        root = Path(directory)
        config = Config(default_config(root), ProjectPaths(root))
        safety = SafetyPolicy(config)
        writer = PdfWriter()
        try:
            for text in texts:
                page = writer.add_blank_page(width=612, height=792)
                font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
                page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
                    DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
                content = DecodedStreamObject()
                content.set_data(b"BT /F1 12 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET")
                page[NameObject("/Contents")] = writer._add_object(content)
            stream = io.BytesIO()
            writer.write(stream)
            raw = stream.getvalue()
        finally:
            writer.close()
        (root / "probe.pdf").write_bytes(raw)
        for index, expected in enumerate(texts, 1):
            result = read_document(config, safety, "probe.pdf", start=index, limit=1)
            if result.status != "success" or expected not in result.output:
                raise RuntimeError("read_failed")
        opened = inspect_document(root / "probe.pdf", "pdf")
        if opened.get("status") != "passed" or opened.get("structure", {}).get("pages") != 2:
            raise RuntimeError("opener_failed")
        text, evidence = extract_pdf_text((root / "probe.pdf").read_bytes())
        if (text != "\n".join(texts) or evidence.get("profile") != "praxile.pdf_page_text.v1"
                or evidence.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest()):
            raise RuntimeError("review_extraction_failed")


def _docx_probe():
    from .config import Config, ProjectPaths, default_config
    from .security import SafetyPolicy
    from .documents import create_docx, read_document
    from .artifact_openers import inspect_document
    from docx import Document
    with tempfile.TemporaryDirectory(prefix="praxile-capability-") as directory:
        root = Path(directory)
        config = Config(default_config(root), ProjectPaths(root))
        safety = SafetyPolicy(config)
        spec = {"blocks": [{"type": "heading", "level": 1, "text": "Probe heading"},
                           {"type": "paragraph", "text": "Probe body"},
                           {"type": "table", "rows": [["Name", "Value"], ["Literal", "2"]]}]}
        if create_docx(config, safety, "probe.docx", spec).status != "success":
            raise RuntimeError("create_failed")
        read = read_document(config, safety, "probe.docx")
        if read.status != "success" or "Probe body" not in read.output:
            raise RuntimeError("read_failed")
        opened = inspect_document(root / "probe.docx", "docx")
        if opened.get("status") != "passed":
            raise RuntimeError("opener_failed")
        document = Document(root / "probe.docx")
        if ([p.text for p in document.paragraphs] != ["Probe heading", "Probe body"]
                or document.paragraphs[0].style.style_id != "Heading1"
                or len(document.tables) != 1
                or [[cell.text for cell in row.cells] for row in document.tables[0].rows]
                    != [["Name", "Value"], ["Literal", "2"]]):
            raise RuntimeError("roundtrip_failed")


def probe(profile):
    if profile not in PROFILES:
        raise ValueError("unsupported capability profile")
    report = {"schema_version": "praxile.capability_probe.v1", "profile": profile,
              "status": "failed", "model_called": False, "formulas_recalculated": False,
              "visual_quality_verified": False}
    try:
        if profile in {"docx_text.v1", "pdf_text.v1"}:
            (_docx_probe if profile == "docx_text.v1" else _pdf_probe)()
            report.update(status="passed", checks=PROFILE_CHECKS[profile],
                          packages={name: version(name) for name in PROFILE_PACKAGES[profile]})
            return report
        from .config import Config, ProjectPaths, default_config
        from .security import SafetyPolicy
        from .documents import create_xlsx, read_document
        from .artifact_openers import inspect_document
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory(prefix="praxile-capability-") as directory:
            root = Path(directory)
            config = Config(default_config(root), ProjectPaths(root))
            safety = SafetyPolicy(config)
            spec = {"sheets": [{"name": "Probe", "rows": [["Name", "Value"], ["=1+1", 2]]}]}
            created = create_xlsx(config, safety, "probe.xlsx", spec)
            if created.status != "success":
                raise RuntimeError("create_failed")
            read = read_document(config, safety, "probe.xlsx", sheet="Probe")
            if read.status != "success":
                raise RuntimeError("read_failed")
            opened = inspect_document(root / "probe.xlsx", "xlsx")
            if opened.get("status") != "passed":
                raise RuntimeError("opener_failed")
            book = load_workbook(root / "probe.xlsx", data_only=False)
            try:
                if (book["Probe"]["A2"].value != "=1+1" or book["Probe"]["A2"].data_type != "s"
                        or book["Probe"]["B2"].value != 2):
                    raise RuntimeError("roundtrip_failed")
            finally:
                book.close()
        report.update(status="passed", checks=PROFILE_CHECKS[profile],
                      packages={name: version(name) for name in PROFILE_PACKAGES[profile]})
    except Exception as error:
        report["error_type"] = type(error).__name__
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    report = probe(parser.parse_args().profile)
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
