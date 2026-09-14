from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from .schema import EvalTask


DEFAULT_DIFF_SCOPE_POLICY = {
    "max_files_changed": 8,
    "max_changed_lines": 400,
    "allow_cross_scope_tests": False,
}


def analyze_diff_scope(
    task: EvalTask,
    patch: str,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    limits = {**DEFAULT_DIFF_SCOPE_POLICY, **dict(policy or {})}
    files = _changed_files(patch)
    added = sum(
        1
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deleted = sum(
        1
        for line in patch.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    source_files = [path for path in files if not _is_test(path)]
    test_files = [path for path in files if _is_test(path)]
    source_scopes = sorted({_scope(path) for path in source_files})
    test_scopes = sorted({_scope(path) for path in test_files})
    cross_scope_tests = sorted(
        path for path in test_files if source_scopes and _scope(path) not in source_scopes
    )
    reasons: list[str] = []
    if len(files) > int(limits["max_files_changed"]):
        reasons.append(f"changed_files={len(files)} exceeds {limits['max_files_changed']}")
    if added + deleted > int(limits["max_changed_lines"]):
        reasons.append(f"changed_lines={added + deleted} exceeds {limits['max_changed_lines']}")
    if cross_scope_tests and not bool(limits["allow_cross_scope_tests"]):
        reasons.append("test changes extend beyond the modified source component")
    status = "passed" if not reasons else "review_required"
    return {
        "schema_version": "praxile.diff_scope.v1",
        "status": status,
        "task_id": task.task_id,
        "patch_digest": "sha256:" + hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "changed_files": files,
        "source_files": source_files,
        "test_files": test_files,
        "source_scopes": source_scopes,
        "test_scopes": test_scopes,
        "cross_scope_test_files": cross_scope_tests,
        "added_lines": added,
        "deleted_lines": deleted,
        "changed_lines": added + deleted,
        "policy": limits,
        "reasons": reasons,
    }


def _changed_files(patch: str) -> list[str]:
    result: list[str] = []
    for match in re.finditer(r"(?m)^diff --git a/(.+?) b/(.+?)$", patch):
        path = match.group(2)
        if path not in result:
            result.append(path)
    return result


def _is_test(path: str) -> bool:
    value = f"/{path.lower()}/"
    name = PurePosixPath(path).name.lower()
    return "/tests/" in value or path.lower().startswith("tests/") or name.startswith("test_")


def _scope(path: str) -> str:
    parts = PurePosixPath(path).parts
    if "tests" in parts:
        index = parts.index("tests")
        return "/".join(parts[:index]) or "tests"
    if len(parts) <= 1:
        return "."
    return "/".join(parts[:-1])
