"""Bounded local artifact checks against public user requirements, never rubrics."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .eval.contracts import OutputContract
from .eval.v2.schema import EvalSchemaError
from .artifact_openers import MEDIA


STRUCTURE_FIELDS = {
    "pdf": {"min_pages": "pages"},
    "pptx": {"min_slides": "slides"},
    "docx": {"min_tables": "tables", "required_headings": "headings"},
    "xlsx": {"required_sheets": "sheets"},
    "text/csv": {"min_rows": "data_rows"},
}


def _structure_checks(requirement: dict, observed: dict) -> list[dict]:
    mapping = STRUCTURE_FIELDS.get(MEDIA.get(requirement["media_type"], requirement["media_type"]), {})
    checks = []
    for name, expected in requirement.get("structure", {}).items():
        actual = observed.get(mapping[name])
        if name.startswith("min_"):
            passed = type(actual) is int and actual >= expected
            checks.append({"requirement": name, "expected": expected, "actual": actual, "passed": passed})
        else:
            missing = [item for item in expected if not isinstance(actual, list) or item not in actual]
            checks.append({"requirement": name, "missing": missing, "passed": not missing})
    return checks


def validate_contract(value: object) -> list[dict]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "source", "outputs"}:
        raise EvalSchemaError("invalid deliverable contract fields")
    if value["schema_version"] != "praxile.deliverable_contract.v1" or value["source"] != "user_request":
        raise EvalSchemaError("deliverables require a versioned public user_request contract")
    outputs = value["outputs"]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= 100:
        raise EvalSchemaError("deliverable contract requires 1 to 100 outputs")
    seen = set()
    for item in outputs:
        if not isinstance(item, dict) or set(item) - {"path", "media_type", "required", "required_fields", "structure"}:
            raise EvalSchemaError("invalid deliverable requirement")
        try:
            output = OutputContract(item["path"], item["media_type"], item.get("required", True))
        except (KeyError, TypeError) as exc:
            raise EvalSchemaError("deliverable path and media_type required") from exc
        if output.path in seen:
            raise EvalSchemaError("duplicate deliverable path")
        seen.add(output.path)
        fields = item.get("required_fields", [])
        if not isinstance(fields, list) or len(fields) > 100 or any(not isinstance(field, str) or not field for field in fields):
            raise EvalSchemaError("required_fields must contain up to 100 non-empty names")
        structure = item.get("structure", {})
        allowed = STRUCTURE_FIELDS.get(MEDIA.get(output.media_type, output.media_type), {})
        if not isinstance(structure, dict) or set(structure) - set(allowed):
            raise EvalSchemaError("unsupported structure constraint for deliverable format")
        for name, expected in structure.items():
            if name.startswith("min_"):
                if type(expected) is not int or not 0 <= expected <= 1_000_000:
                    raise EvalSchemaError("structural minimum must be an integer in [0, 1000000]")
            elif (not isinstance(expected, list) or not 1 <= len(expected) <= 100
                  or any(not isinstance(entry, str) or not entry.strip() or len(entry) > 200 for entry in expected)):
                raise EvalSchemaError("named structure requirements must contain 1 to 100 bounded names")
    return outputs


def verify_deliverables(root: Path, contract: dict, *, max_bytes: int = 10_000_000) -> dict:
    """No code execution, network access, globbing, or symlink traversal.

    Checks represent structural evidence only. source labels are not proof of
    provenance; callers must supply the approved public contract.
    """
    outputs = validate_contract(contract)
    if type(max_bytes) is not int or not 1 <= max_bytes <= 100_000_000:
        raise ValueError("max_bytes must be in [1, 100000000]")
    root = root.resolve(strict=True)
    results = []
    for requirement in outputs:
        path = root / requirement["path"]
        result = {"path": requirement["path"], "media_type": requirement["media_type"],
                  "required": requirement.get("required", True), "status": "failed"}
        results.append(result)
        if any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root)):
            result["reason"] = "symlink_forbidden"
            continue
        try:
            mode = path.stat().st_mode
            if not stat.S_ISREG(mode):
                result["reason"] = "not_a_regular_file"
                continue
            with path.open("rb") as handle:
                content = handle.read(max_bytes + 1)
        except FileNotFoundError:
            result.update(status="missing" if result["required"] else "optional_missing", reason="not_found")
            continue
        except OSError as exc:
            result["reason"] = type(exc).__name__
            continue
        if len(content) > max_bytes:
            result["reason"] = "size_limit"
            continue
        result.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        fields = requirement.get("required_fields", [])
        try:
            media = requirement["media_type"]
            if media == "application/json":
                def reject_constant(value):
                    raise ValueError("nonfinite JSON constant")
                value = json.loads(content, parse_constant=reject_constant)
                if fields and not isinstance(value, dict):
                    raise ValueError("object required")
                missing = [field for field in fields if field not in value] if fields else []
            elif media == "text/csv":
                rows = csv.reader(io.StringIO(content.decode("utf-8-sig")), strict=True)
                header = next(rows, None)
                if not header or len(header) != len(set(header)):
                    raise ValueError("unique CSV header required")
                count = 0
                for row in rows:
                    if len(row) != len(header):
                        raise ValueError("CSV row width mismatch")
                    count += 1
                result["data_rows"] = count
                missing = [field for field in fields if field not in header]
            elif media in {"text/plain", "text/markdown"}:
                content.decode("utf-8")
                if not content.strip():
                    raise ValueError("empty text")
                if fields:
                    result.update(status="unsupported", reason="text_structure_checks_not_supported")
                    continue
                missing = []
            elif media in MEDIA:
                if fields:
                    result.update(status="unsupported", reason="document_field_requirements_not_supported")
                    continue
                # Parse an immutable private copy, not an agent-mutated original.
                with tempfile.TemporaryDirectory(prefix="praxile-document-check-") as directory:
                    source = Path(directory) / ("artifact." + MEDIA[media])
                    source.write_bytes(content)
                    try:
                        child = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("artifact_openers.py")),
                                                str(source), MEDIA[media]], capture_output=True, timeout=15)
                    except subprocess.TimeoutExpired:
                        result.update(status="failed", reason="document_parser_timeout")
                        continue
                    except OSError:
                        result.update(status="failed", reason="document_parser_unavailable")
                        continue
                if child.returncode != 0:
                    result.update(status="failed", reason="document_parser_process_failed")
                    continue
                parsed = json.loads(child.stdout)
                if not isinstance(parsed, dict) or parsed.get("status") not in {"passed", "failed", "unsupported"}:
                    raise ValueError("invalid parser result")
                result.update(parsed)
                if parsed["status"] == "passed":
                    checks = _structure_checks(requirement, parsed.get("structure", {}))
                    result["structure_checks"] = checks
                    if not all(check["passed"] for check in checks):
                        result.update(status="failed", reason="user_structure_requirement_unmet")
                continue
            else:
                result.update(status="unsupported", reason="format_opener_not_available")
                continue
            result.update(status="failed" if missing else "passed", missing_fields=missing)
            checks = _structure_checks(requirement, result)
            result["structure_checks"] = checks
            if not all(check["passed"] for check in checks):
                result.update(status="failed", reason="user_structure_requirement_unmet")
        except (ValueError, UnicodeError, csv.Error, RecursionError):
            result["reason"] = "invalid_structure_or_encoding"
    passed = all(item["status"] in {"passed", "optional_missing"} for item in results)
    return {"schema_version": "praxile.deliverable_check.v1", "passed": passed, "outputs": results,
            "official_evaluation": False, "semantic_quality_verified": False,
            "contract_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
