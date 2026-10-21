import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.documents import create_xlsx
from praxile.runtime import AgentRuntime
from praxile.security import SafetyPolicy

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_xlsx_dispatch_literal_cells_and_guarded_rollback(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    runtime = AgentRuntime(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    spec = {"sheets": [{"name": "Inventory", "rows": [["Name", "Count", "Active"], ["Drug", 2, True]]},
                       {"name": "Literal", "rows": [["=1+1", "#N/A", "https://example.org"], [None, 1.25, "尾行"]]}]}
    try:
        obs = runtime.tools.execute({"type": "create_xlsx", "path": "inventory.xlsx", "workbook": spec}, task_id="xlsx", step=1)
        assert obs["status"] == "success"
        target = tmp_path / "inventory.xlsx"
        workbook = openpyxl.load_workbook(target, data_only=False)
        try:
            assert workbook.sheetnames == ["Inventory", "Literal"]
            assert workbook["Inventory"]["B2"].value == 2
            assert workbook["Inventory"]["C2"].value is True
            assert workbook["Literal"]["A1"].value == "=1+1" and workbook["Literal"]["A1"].data_type == "s"
            assert workbook["Literal"]["B1"].data_type == "s"
            assert workbook["Literal"]["C1"].hyperlink is None
            assert workbook["Literal"]["C2"].value == "尾行"
        finally:
            workbook.close()
        original = target.read_bytes()
        assert create_xlsx(runtime.config, runtime.safety, "inventory.xlsx", spec).status == "blocked"
        assert target.read_bytes() == original
        trajectory = {"actions": [{"action_type": "create_xlsx", "status": "success", "observation": obs}]}
        target.write_bytes(b"human changes")
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "skipped_modified"
        target.write_bytes(original)
        assert runtime.project.rollback_task(trajectory)[0]["mode"] == "deleted_new_file"
        assert not target.exists() and not list(tmp_path.glob(".praxile-xlsx-*"))
    finally:
        runtime.tools.close()


def test_xlsx_invalid_destination_or_spec_does_not_publish(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    safety = SafetyPolicy(config)
    spec = {"sheets": [{"name": "Data", "rows": [[1]]}]}
    assert create_xlsx(config, safety, "../escape.xlsx", spec).status == "blocked"
    assert create_xlsx(config, safety, "missing/data.xlsx", spec).status == "blocked"
    assert create_xlsx(config, safety, "data.xlsx", {"script": "code"}).status == "failure"
    assert not list(tmp_path.iterdir())
