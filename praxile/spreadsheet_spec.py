"""Bounded value-only workbook specification; strings never become formulas."""
import json
import math
import re


def xlsx_spec_prompt():
    return ('create_xlsx.workbook requires {"sheets":[{"name":"Sheet1","rows":[["Name","Count"],["Item",2]]}]}. '
            '1..10 uniquely named sheets, rectangular rows (1..1000 by 1..100), at most 20000 cells and 256 KiB. '
            'Cells are strings, finite numbers, booleans or null; all strings are literal text, never formulas. '
            'Existing parent directory and new .xlsx path required. No overwrite, macros, links or formula recalculation.')


def validate_xlsx_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"sheets"}:
        raise ValueError("workbook requires sheets only")
    sheets = spec["sheets"]
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 10:
        raise ValueError("workbook requires 1..10 sheets")
    if len(json.dumps(spec, ensure_ascii=False, allow_nan=False).encode()) > 256 * 1024:
        raise ValueError("workbook exceeds 256 KiB")
    names, count = set(), 0
    for sheet in sheets:
        if not isinstance(sheet, dict) or set(sheet) != {"name", "rows"}:
            raise ValueError("sheet requires name and rows only")
        name, rows = sheet["name"], sheet["rows"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 31 or not name.strip()
                or re.search(r"[\\/*?:\[\]\x00-\x1f]", name) or name.startswith("'") or name.endswith("'")
                or name.casefold() in names):
            raise ValueError("invalid or duplicate sheet name")
        names.add(name.casefold())
        if (not isinstance(rows, list) or not 1 <= len(rows) <= 1000
                or not isinstance(rows[0], list) or not 1 <= len(rows[0]) <= 100):
            raise ValueError("sheet requires 1..1000 rows and 1..100 columns")
        for row in rows:
            if not isinstance(row, list) or len(row) != len(rows[0]):
                raise ValueError("sheet rows must be rectangular")
            count += len(row)
            if count > 20000:
                raise ValueError("workbook exceeds 20000 cells")
            for value in row:
                if value is None or type(value) is bool:
                    continue
                if type(value) in (int, float):
                    if abs(value) > 1e15 or not math.isfinite(value):
                        raise ValueError("cell number is nonfinite or exceeds supported magnitude")
                elif isinstance(value, str):
                    if len(value) > 32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value):
                        raise ValueError("cell text exceeds bounds or contains unsupported controls")
                else:
                    raise ValueError("cells must be literal scalar values")
    return spec
