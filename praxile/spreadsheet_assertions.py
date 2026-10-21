"""Public literal-cell requirements, not computed formula or semantic scores."""
import math
import re

from .eval.v2.schema import EvalSchemaError


def validate_cells(cells):
    if not isinstance(cells, list) or not 1 <= len(cells) <= 100:
        raise EvalSchemaError("cell_values requires 1..100 cells")
    seen = set()
    for item in cells:
        if not isinstance(item, dict) or set(item) != {"sheet", "cell", "value"}:
            raise EvalSchemaError("cell requirement needs sheet, cell and value")
        sheet, cell, value = item["sheet"], item["cell"], item["value"]
        match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", cell) if isinstance(cell, str) else None
        if (not isinstance(sheet, str) or not 1 <= len(sheet) <= 31 or not sheet.strip()
                or re.search(r"[\\/*?:\[\]\x00-\x1f]", sheet) or match is None):
            raise EvalSchemaError("invalid sheet or canonical cell address")
        column = 0
        for char in match[1]:
            column = column * 26 + ord(char) - ord("A") + 1
        if column > 16384 or int(match[2]) > 1048576 or (sheet, cell) in seen:
            raise EvalSchemaError("duplicate or out-of-range cell requirement")
        seen.add((sheet, cell))
        if type(value) in (int, float):
            if abs(value) > 1e15 or not math.isfinite(value):
                raise EvalSchemaError("cell requirement number exceeds bounds")
        elif isinstance(value, str):
            if len(value) > 2000:
                raise EvalSchemaError("cell requirement text exceeds bounds")
        elif value is not None and type(value) is not bool:
            raise EvalSchemaError("cell requirement must be a literal scalar")


def check_cells(expected, observed):
    checks = []
    observed = observed if isinstance(observed, list) else []
    for item in expected:
        matches = [row for row in observed if isinstance(row, dict)
                   and row.get("sheet") == item["sheet"] and row.get("cell") == item["cell"]]
        actual = matches[0] if len(matches) == 1 else {"status": "unknown"}
        value, wanted = actual.get("value"), item["value"]
        same_type = type(value) is type(wanted) or (type(value) in (int, float) and type(wanted) in (int, float))
        checks.append({"requirement": "cell_values", "sheet": item["sheet"], "cell": item["cell"],
                       "expected": wanted, "actual": value, "observation_status": actual.get("status", "unknown"),
                       "passed": actual.get("status") == "observed" and same_type and value == wanted})
    return checks
