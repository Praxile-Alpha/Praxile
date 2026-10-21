"""Fixed read-only parser subprocess. No shell, macros, rendering, or recalculation."""
import json
import sys
import zipfile
from pathlib import Path


def docx_body_blocks(path):
    import docx
    from docx.oxml.ns import qn
    document = docx.Document(path)
    return [" ".join(node.text or "" for node in block.iter(qn("w:t")))
            for block in document.element.body if block.tag in {qn("w:p"), qn("w:tbl")}]


def extract(path, *, start, limit, sheet, offset):
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".docx"}:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 64 * 1024 * 1024:
                raise ValueError("expanded document exceeds limit")
    units = []

    def add(index, text):
        units.append({"index": index, "text": text[offset:offset + 4000],
                      "char_offset": offset, "total_chars": len(text),
                      "next_char_offset": offset + 4000 if len(text) > offset + 4000 else None})

    metadata = {}
    if suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as document:
            total = len(document.pages)
            for index in range(start - 1, min(total, start - 1 + limit)):
                add(index + 1, document.pages[index].extract_text() or "")
        kind = "page"
        metadata["limitation"] = "Text extraction only; empty pages may require OCR. No visual verification."
    elif suffix == ".xlsx":
        import openpyxl
        document = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            metadata["sheets"] = document.sheetnames
            selected = document[sheet] if sheet else document.worksheets[0]
            total = selected.max_row or 0
            metadata.update(sheet=selected.title, columns=selected.max_column,
                            columns_truncated=(selected.max_column or 0) > 128,
                            limitation="Formula text only; no recalculation or cached-value verification.")
            for index, row in enumerate(selected.iter_rows(min_row=start,
                    max_row=min(total, start + limit - 1), max_col=min(selected.max_column or 1, 128),
                    values_only=True), start):
                if index > total:
                    break
                add(index, json.dumps(list(row), ensure_ascii=False, default=str))
        finally:
            document.close()
        kind = "row"
    elif suffix == ".docx":
        # Paragraph and table traversal follows document order, not two separate lists.
        texts = docx_body_blocks(path)
        total = len(texts)
        for index in range(start - 1, min(total, start - 1 + limit)):
            add(index + 1, texts[index])
        kind = "block"
        metadata["limitation"] = "Body text and tables only; no headers, comments, tracked-change interpretation, or layout verification."
    else:
        raise ValueError("unsupported format")
    return {"unit_kind": kind, "total_units": total, "units": units,
            "next_start": start + len(units) if units and start + len(units) <= total else None,
            **metadata}


def main():
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    path, options = Path(sys.argv[1]), json.loads(sys.argv[2])
    try:
        if options.get("operation") == "create_docx":
            import docx
            from docx.shared import Pt, Inches
            document = docx.Document()
            document.styles["Normal"].font.name = "Calibri"
            document.styles["Normal"].font.size = Pt(11)
            spec = json.loads(path.read_text())
            compact = spec.get("layout") == "compact"
            if compact:
                section = document.sections[0]
                section.page_width, section.page_height = Inches(8.5), Inches(11)
                section.top_margin = section.bottom_margin = Inches(0.6)
                section.left_margin = section.right_margin = Inches(0.6)
                for name, size in (("Normal", 10), ("List Bullet", 10),
                                   ("Heading 1", 14), ("Heading 2", 12), ("Heading 3", 11)):
                    style = document.styles[name]
                    style.font.size = Pt(size)
                    style.paragraph_format.line_spacing = 1
                    style.paragraph_format.space_before = Pt(0)
                    style.paragraph_format.space_after = Pt(3)
            for block in spec["blocks"]:
                kind = block["type"]
                if kind == "heading":
                    document.add_heading(block["text"], level=block.get("level", 1))
                elif kind == "page_break":
                    document.add_page_break()
                elif kind == "table":
                    table = document.add_table(rows=0, cols=len(block["rows"][0]))
                    table.style = "Table Grid"
                    for index, row in enumerate(block["rows"]):
                        table_row = table.add_row()
                        if compact:
                            from docx.oxml import OxmlElement
                            properties = table_row._tr.get_or_add_trPr()
                            properties.append(OxmlElement("w:cantSplit"))
                            if index == 0:
                                properties.append(OxmlElement("w:tblHeader"))
                        for cell, text in zip(table_row.cells, row):
                            cell.text = text
                            if compact:
                                for paragraph in cell.paragraphs:
                                    paragraph.paragraph_format.space_after = Pt(0)
                                    paragraph.paragraph_format.line_spacing = 1
                                    for run in paragraph.runs:
                                        run.font.size = Pt(9.5)
                                        run.bold = index == 0
                else:
                    document.add_paragraph(block["text"], style="List Bullet" if kind == "bullet" else None)
            document.save(path.parent / "result.docx")
            result = {"status": "success"}
        elif options.get("operation") == "review_pdf_text":
            from importlib.metadata import version
            from pypdf import PdfReader
            reader = PdfReader(path, strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 100:
                raise ValueError("encrypted or oversized PDF")
            pages, length = [], 0
            for page in reader.pages:
                text = page.extract_text()
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("PDF page has no extractable text; OCR or review required")
                length += len(text) + (1 if pages else 0)
                if length > 200000:
                    raise ValueError("PDF text exceeds review bounds")
                pages.append(text)
            result = {"status": "success", "text": "\n".join(pages), "parser_version": version("pypdf")}
        elif options.get("operation") == "review_docx_body":
            from importlib.metadata import version
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if (len(entries) > 10000 or len({item.filename for item in entries}) != len(entries)
                        or sum(item.file_size for item in entries) > 64 * 1024**2):
                    raise ValueError("document package exceeds review bounds")
            blocks = docx_body_blocks(path)
            text = "\n".join(blocks)
            if len(blocks) > 10000 or len(text) > 200000:
                raise ValueError("document text exceeds review bounds")
            result = {"status": "success", "text": text, "parser_version": version("python-docx")}
        else:
            result = {"status": "success", "data": extract(path, **options)}
    except ImportError:
        result = {"status": "failure", "error": "Missing document parser; install praxile[documents]."}
    except Exception as error:
        result = {"status": "failure", "error": "Document parsing failed: " + type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
