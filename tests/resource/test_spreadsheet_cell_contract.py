import hashlib
import json

import pytest

from praxile.deliverables import verify_deliverables
from praxile.config import Config, ProjectPaths, default_config
from praxile.runtime import AgentRuntime

pytestmark = [pytest.mark.resource, pytest.mark.integration]
MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def contract(value, sheet="Inventory", cell="A2"):
    return {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "inventory.xlsx", "media_type": MEDIA,
         "structure": {"cell_values": [{"sheet": sheet, "cell": cell, "value": value}]}}]}


def save(tmp_path, value):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "Inventory"
    book.active["A1"] = "Count"
    book.active["A2"] = value
    book.save(tmp_path / "inventory.xlsx")
    book.close()


@pytest.mark.parametrize("value,expected,passed", [(2, 2, True), (2, 3, False), (True, 1, False),
    ("2", 2, False), ("=1+1", 2, False), ("#N/A", "#N/A", False), (None, None, True)])
def test_actual_cell_values_not_sheet_existence(tmp_path, value, expected, passed):
    save(tmp_path, value)
    result = verify_deliverables(tmp_path, contract(expected))
    assert result["passed"] is passed
    assert result["semantic_quality_verified"] is False and result["official_evaluation"] is False
    assert result["outputs"][0]["structure"]["formulas_recalculated"] is False


def test_missing_sheet_is_not_empty_cell(tmp_path):
    save(tmp_path, 2)
    assert not verify_deliverables(tmp_path, contract(None, sheet="Missing"))["passed"]
    assert verify_deliverables(tmp_path, contract(None, cell="B5"))["passed"]


def test_false_declared_dimensions_cannot_hide_wrong_value(tmp_path):
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    save(tmp_path, 99)
    path = tmp_path / "inventory.xlsx"
    revised = io.BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(revised, "w") as target:
        for item in source.infolist():
            raw = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                document = ET.fromstring(raw)
                document.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}dimension").set("ref", "A1:A1")
                raw = ET.tostring(document)
            target.writestr(item, raw)
    path.write_bytes(revised.getvalue())
    assert not verify_deliverables(tmp_path, contract(None))["passed"]
    assert verify_deliverables(tmp_path, contract(99))["passed"]


def test_runtime_repairs_wrong_value_then_passes_frozen_contract(tmp_path, monkeypatch):
    save(tmp_path, 99)
    raw = (tmp_path / "inventory.xlsx").read_bytes()
    runtime = AgentRuntime(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    actions = iter([{"type": "finish", "summary": "ready"},
        {"type": "revise_xlsx", "path": "inventory.xlsx", "expected_sha256": hashlib.sha256(raw).hexdigest(),
         "workbook": {"sheets": [{"name": "Inventory", "rows": [["Count"], [2]]}]}},
        {"type": "finish", "summary": "corrected"}])
    monkeypatch.setattr(runtime.router, "chat", lambda *a, **k: {
        "content": json.dumps(next(actions)), "usage": {}, "route": {}})
    try:
        result = runtime.run("Set count to 2", deliverable_contract=contract(2), max_steps=3, use_experience=False)
        assert result["result"]["status"] == "completed"
        gates = [a["status"] for a in result["actions"] if a["action_type"] == "deliverable_finish_gate"]
        assert gates == ["failure", "success"]
    finally:
        runtime.tools.close()
