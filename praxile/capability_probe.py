"""Local functional capability probes; no model, network or task data."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import tempfile

PROFILES = ("xlsx_values.v1",)


def probe(profile):
    if profile not in PROFILES:
        raise ValueError("unsupported capability profile")
    report = {"schema_version": "praxile.capability_probe.v1", "profile": profile,
              "status": "failed", "model_called": False, "formulas_recalculated": False,
              "visual_quality_verified": False}
    try:
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
        report.update(status="passed", checks=["create", "read", "opener", "literal_roundtrip"],
                      packages={name: version(name) for name in ("openpyxl", "et-xmlfile", "defusedxml")})
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
