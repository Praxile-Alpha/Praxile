"""Bounded local artifact checks against public user requirements, never rubrics."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .eval.contracts import OutputContract
from .eval.v2.schema import EvalSchemaError
from .artifact_openers import MEDIA
from .experience_conditions import check_conditions


STRUCTURE_FIELDS = {
    "pdf": {"min_pages": "pages"},
    "pptx": {"min_slides": "slides"},
    "docx": {"min_tables": "tables", "required_headings": "headings", "max_pages": "pages"},
    "xlsx": {"required_sheets": "sheets"},
    "text/csv": {"min_rows": "data_rows"},
}


def _structure_checks(requirement: dict, observed: dict) -> list[dict]:
    mapping = STRUCTURE_FIELDS.get(MEDIA.get(requirement["media_type"], requirement["media_type"]), {})
    checks = []
    for name, expected in requirement.get("structure", {}).items():
        actual = observed.get(mapping[name])
        if name.startswith(("min_", "max_")):
            passed = type(actual) is int and (actual >= expected if name.startswith("min_") else actual <= expected)
            checks.append({"requirement": name, "expected": expected, "actual": actual, "passed": passed})
        else:
            missing = [item for item in expected if not isinstance(actual, list) or item not in actual]
            checks.append({"requirement": name, "missing": missing, "passed": not missing})
    return checks


def validate_contract(value: object) -> list[dict]:
    if not isinstance(value, dict):
        raise EvalSchemaError("invalid deliverable contract fields")
    version = value.get("schema_version")
    fields = {"schema_version", "source", "outputs"}
    if version in {"praxile.deliverable_contract.v2", "praxile.deliverable_contract.v3"}:
        fields.add("state_equals")
    if version == "praxile.deliverable_contract.v3":
        fields.add("unchanged_files")
    if set(value) != fields:
        raise EvalSchemaError("invalid deliverable contract fields")
    if version not in {"praxile.deliverable_contract.v1", "praxile.deliverable_contract.v2", "praxile.deliverable_contract.v3"} or value["source"] != "user_request":
        raise EvalSchemaError("deliverables require a versioned public user_request contract")
    states = value.get("state_equals", {})
    if version in {"praxile.deliverable_contract.v2", "praxile.deliverable_contract.v3"}:
        if not isinstance(states, dict) or not (0 if version.endswith("v3") else 1) <= len(states) <= 20:
            raise EvalSchemaError("invalid number of state constraints")
        for key, expected in states.items():
            if (not isinstance(key, str) or len(key) > 120
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,4}", key)
                    or type(expected) not in (str, bool, int)
                    or isinstance(expected, str) and len(expected) > 2000):
                raise EvalSchemaError("invalid bounded state constraint")
    unchanged = value.get("unchanged_files", {})
    if version == "praxile.deliverable_contract.v3":
        if not isinstance(unchanged, dict) or not 1 <= len(unchanged) <= 100:
            raise EvalSchemaError("v3 requires 1 to 100 unchanged files")
        for path, digest in unchanged.items():
            OutputContract(path, "application/octet-stream")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise EvalSchemaError("unchanged files require lowercase SHA-256")
    outputs = value["outputs"]
    if not isinstance(outputs, list) or not (0 if states or unchanged else 1) <= len(outputs) <= 100:
        raise EvalSchemaError("deliverable contract requires outputs or v2 state constraints")
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
            if name == "max_pages":
                if type(expected) is not int or not 1 <= expected <= 2000:
                    raise EvalSchemaError("max_pages must be an integer in [1, 2000]")
            elif name.startswith("min_"):
                if type(expected) is not int or not 0 <= expected <= 1_000_000:
                    raise EvalSchemaError("structural minimum must be an integer in [0, 1000000]")
            elif (not isinstance(expected, list) or not 1 <= len(expected) <= 100
                  or any(not isinstance(entry, str) or not entry.strip() or len(entry) > 200 for entry in expected)):
                raise EvalSchemaError("named structure requirements must contain 1 to 100 bounded names")
    return outputs


def verify_deliverables(root: Path, contract: dict, *, max_bytes: int = 10_000_000,
                        observed_state: dict | None = None, observed_at: str | None = None,
                        observation_source: str = "caller_supplied", layout_checker=None) -> dict:
    """No model code execution, globbing, or symlink traversal.

    Checks represent structural evidence only. source labels are not proof of
    provenance; callers must supply the approved public contract. Optional
    layout_checker is a trusted caller service, never part of model JSON.
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
                    page_limit = requirement.get("structure", {}).get("max_pages")
                    if page_limit is not None:
                        if layout_checker is None:
                            result.update(status="unsupported", reason="layout_renderer_unavailable")
                            continue
                        layout = layout_checker(requirement["path"], page_limit)
                        result["layout_check"] = layout
                        data = layout.get("data", {})
                        if data.get("source_sha256") != result["sha256"]:
                            result.update(status="failed", reason="layout_snapshot_unverified")
                            continue
                        if layout.get("status") != "success" or data.get("page_limit_met") is not True:
                            result.update(status="failed", reason="layout_requirement_unverified")
                            continue
                        result.setdefault("structure", {})["pages"] = data.get("pages")
                        result["rendered"] = True
                    checks = _structure_checks(requirement, result.get("structure", {}))
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
    report = {"schema_version": "praxile.deliverable_check.v1", "passed": passed, "outputs": results,
            "official_evaluation": False, "semantic_quality_verified": False,
            "contract_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    if contract["schema_version"] in {"praxile.deliverable_contract.v2", "praxile.deliverable_contract.v3"}:
        state_check = check_conditions({"state_equals": contract["state_equals"]} if contract["state_equals"] else None,
                                       {"state": observed_state})
        evidence = []
        for name in contract["state_equals"]:
            actual = observed_state
            for key in name.split("."):
                actual = actual.get(key) if isinstance(actual, dict) else None
            item = {"name": name, "availability": "observed" if actual is not None else "unknown"}
            if type(actual) in (str, int, bool):
                try:
                    encoded = json.dumps(actual, ensure_ascii=True).encode()
                except (ValueError, OverflowError):
                    encoded = None
                if encoded is not None:
                    item["value_sha256"] = hashlib.sha256(encoded).hexdigest()
                    if len(encoded) <= 4096:
                        item["value"] = actual
                    else:
                        item["value_omitted"] = "size_limit"
                else:
                    item["value_omitted"] = "serialization_limit"
            elif actual is not None:
                item["value_omitted"] = "unsupported_type"
            evidence.append(item)
        state_evidence = {"schema_version": "praxile.deliverable_state_evidence.v1",
                          "source": observation_source, "observed_at": observed_at, "fields": evidence,
                          "provenance_attested": False}
        digest = hashlib.sha256(json.dumps(state_evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        report.update(schema_version="praxile.deliverable_check.v2", state_check=state_check,
                      state_evidence=state_evidence, state_evidence_sha256=digest,
                      passed=passed and state_check["status"] in {"matched", "not_required"},
                      state_transitions_verified=False, contract_provenance_verified=False)
    if contract["schema_version"] == "praxile.deliverable_contract.v3":
        preserved = []
        for name, expected in contract["unchanged_files"].items():
            path = root / name
            item = {"path": name, "expected_sha256": expected, "passed": False}
            preserved.append(item)
            if any(part.is_symlink() for part in (path, *path.parents) if part != root and part.is_relative_to(root)):
                item["reason"] = "symlink_forbidden"
                continue
            try:
                if not stat.S_ISREG(path.stat().st_mode):
                    item["reason"] = "not_a_regular_file"
                    continue
                with path.open("rb") as handle:
                    content = handle.read(max_bytes + 1)
                if len(content) > max_bytes:
                    item["reason"] = "size_limit"
                    continue
                actual = hashlib.sha256(content).hexdigest()
                item.update(actual_sha256=actual, size_bytes=len(content), passed=actual == expected,
                            reason="matched" if actual == expected else "content_changed")
            except FileNotFoundError:
                item["reason"] = "not_found"
            except OSError as error:
                item["reason"] = type(error).__name__
        report.update(schema_version="praxile.deliverable_check.v3", unchanged_files=preserved,
                      passed=report["passed"] and all(item["passed"] for item in preserved),
                      preservation_scope="observed_final_bytes_only")
    return report
