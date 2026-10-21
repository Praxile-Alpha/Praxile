import pytest

from praxile.deliverables import validate_contract
from praxile.spreadsheet_assertions import validate_cells, check_cells


@pytest.mark.parametrize("cell", ["a1", "$A$1", "A0", "XFE1", "A1048577", "A1:B2", 1])
def test_invalid_addresses(cell):
    with pytest.raises(ValueError):
        validate_cells([{"sheet": "Data", "cell": cell, "value": 1}])


@pytest.mark.parametrize("value", [[], {}, float("nan"), float("inf"), 10**100, "x" * 2001])
def test_invalid_literal(value):
    with pytest.raises(ValueError):
        validate_cells([{"sheet": "Data", "cell": "A1", "value": value}])


def test_duplicates_and_type_sensitive_comparison():
    item = {"sheet": "Data", "cell": "A1", "value": 1}
    with pytest.raises(ValueError):
        validate_cells([item, item])
    for actual, status, passed in [(1.0, "observed", True), (True, "observed", False),
                                   (1, "formula_or_error_unverified", False), (None, "unknown", False)]:
        assert check_cells([item], [{**item, "value": actual, "status": status}])[0]["passed"] is passed
    assert not check_cells([{**item, "value": None}], [])[0]["passed"]


def test_cell_values_are_xlsx_only_public_requirements():
    contract = {"schema_version": "praxile.deliverable_contract.v1", "source": "user_request", "outputs": [
        {"path": "inventory.xlsx", "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "structure": {"cell_values": [{"sheet": "Inventory", "cell": "A2", "value": 2}]}}]}
    validate_contract(contract)
    contract["outputs"][0]["media_type"] = "application/pdf"
    with pytest.raises(ValueError):
        validate_contract(contract)
