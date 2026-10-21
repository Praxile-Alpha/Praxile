import pytest

from praxile.action_schema import ActionSchemaRegistry
from praxile.spreadsheet_spec import validate_xlsx_spec
from praxile.tools import MUTATING_ACTIONS


def spec(value=1):
    return {"sheets": [{"name": "Data", "rows": [["header"], [value]]}]}


@pytest.mark.parametrize("value", [None, True, 123, 1.25, "=SUM(A1)", "+cmd", "@literal", "中文"])
def test_scalar_workbooks_are_valid(value):
    action = {"type": "create_xlsx", "path": "data.xlsx", "workbook": spec(value)}
    assert ActionSchemaRegistry().validate(action)[0]
    assert "create_xlsx" in MUTATING_ACTIONS


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**100, [], {}, "bad\x00text", "x" * 32768])
def test_invalid_cells_rejected(value):
    assert not ActionSchemaRegistry().validate({"type": "create_xlsx", "path": "data.xlsx", "workbook": spec(value)})[0]


@pytest.mark.parametrize("name", ["", " ", "a/b", "a:b", "'Name", "Name'", "a" * 32])
def test_invalid_sheet_names(name):
    workbook = spec()
    workbook["sheets"][0]["name"] = name
    with pytest.raises(ValueError):
        validate_xlsx_spec(workbook)


def test_rectangular_unique_and_bounded_sheets():
    for workbook in (
        {"sheets": [{"name": "Data", "rows": [[1], [1, 2]]}]},
        {"sheets": [{"name": "Data", "rows": [[1]]}, {"name": "data", "rows": [[2]]}]},
        {"sheets": [{"name": "Data", "rows": [[1] * 100] * 201}]},
        {"sheets": [], "template": "script"},
    ):
        with pytest.raises(ValueError):
            validate_xlsx_spec(workbook)
