"""Exact, fail-closed applicability checks using control-plane observations."""
from __future__ import annotations

import importlib.metadata
import platform
import re
from collections.abc import Mapping


def observe_tool_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in ("playwright", "openpyxl", "python-docx", "python-pptx", "pypdf"):
        try:
            versions["python_package:" + name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return versions


def check_conditions(requirements: object, observations: Mapping | None) -> dict:
    if requirements is None:
        return {"status": "not_required", "checks": []}
    if not isinstance(requirements, dict) or not requirements or set(requirements) - {"tool_versions", "state_equals"}:
        return {"status": "abstained", "reason": "invalid activation requirements", "checks": []}
    observed = observations if isinstance(observations, Mapping) else {}
    checks = []
    for category, expected in requirements.items():
        if not isinstance(expected, dict) or not 1 <= len(expected) <= 20:
            return {"status": "abstained", "reason": "invalid condition map", "checks": checks}
        for name, value in expected.items():
            if not isinstance(name, str) or not 1 <= len(name) <= 120:
                return {"status": "abstained", "reason": "invalid condition name", "checks": checks}
            if category == "tool_versions":
                if not isinstance(value, str) or not value.strip() or len(value) > 100:
                    return {"status": "abstained", "reason": "invalid version requirement", "checks": checks}
                versions = observed.get("tool_versions")
                actual = versions.get(name) if isinstance(versions, Mapping) else None
            else:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,4}", name) or type(value) not in (str, bool, int):
                    return {"status": "abstained", "reason": "invalid state requirement", "checks": checks}
                actual = observed.get("state", {})
                for key in name.split("."):
                    actual = actual.get(key) if isinstance(actual, Mapping) else None
            state = "unknown" if actual is None else "matched" if type(actual) is type(value) and actual == value else "mismatched"
            checks.append({"category": category, "name": name, "status": state})
    matched = all(item["status"] == "matched" for item in checks)
    return {"status": "matched" if matched else "abstained", "checks": checks,
            "reason": "all observed conditions match" if matched else "required state/version is unknown or mismatched"}
