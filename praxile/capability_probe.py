"""Local functional capability probes; no model, network or task data."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import tempfile

PROFILE_PACKAGES = {
    "xlsx_values.v1": ("openpyxl", "et-xmlfile", "defusedxml"),
    "docx_text.v1": ("python-docx", "lxml", "defusedxml"),
}
PROFILE_CHECKS = {
    "xlsx_values.v1": ["create", "read", "opener", "literal_roundtrip"],
    "docx_text.v1": ["create", "read", "opener", "heading_table_roundtrip"],
}
PROFILES = tuple(PROFILE_PACKAGES)


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
        if profile == "docx_text.v1":
            _docx_probe()
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
