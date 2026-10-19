"""Fixed read-only parser subprocess. No shell, macros, rendering, or recalculation."""
import json
import sys
import zipfile
from pathlib import Path


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
        import docx
        document = docx.Document(path)
        # Paragraph and table traversal follows document order, not two separate lists.
        from docx.oxml.ns import qn
        texts = []
        for block in document.element.body:
            if block.tag in {qn("w:p"), qn("w:tbl")}:
                texts.append(" ".join(node.text or "" for node in block.iter(qn("w:t"))))
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
        result = {"status": "success", "data": extract(path, **options)}
    except ImportError:
        result = {"status": "failure", "error": "Missing document parser; install praxile[documents]."}
    except Exception as error:
        result = {"status": "failure", "error": "Document parsing failed: " + type(error).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
