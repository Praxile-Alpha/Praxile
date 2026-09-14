from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


MINI_SWE_PREFLIGHT_SCHEMA_VERSION = "praxile.mini_swe_preflight.v1"


@dataclass(frozen=True)
class MiniSwePreflightReport:
    status: str
    workspace_mode: str
    workspace_root: str
    git_root: str | None
    checks: tuple[Mapping[str, Any], ...]
    blocking_reasons: tuple[str, ...]
    schema_version: str = MINI_SWE_PREFLIGHT_SCHEMA_VERSION

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "workspace_mode": self.workspace_mode,
            "workspace_root": self.workspace_root,
            "git_root": self.git_root,
            "checks": [dict(item) for item in self.checks],
            "blocking_reasons": list(self.blocking_reasons),
        }


def run_mini_swe_preflight(
    root: Path,
    *,
    settings: Mapping[str, Any],
    config_specs: Sequence[str],
) -> MiniSwePreflightReport:
    resolved_root = root.expanduser().resolve()
    workspace_mode = str(settings.get("workspace_mode") or "local").strip().lower()
    require_git = bool(settings.get("require_git_workspace", False))
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []

    mode_ok = workspace_mode == "local"
    checks.append(
        {
            "name": "workspace_mode",
            "status": "passed" if mode_ok else "failed",
            "detail": workspace_mode,
        }
    )
    if not mode_ok:
        blockers.append(
            "MiniSweAgentAdapter runs the local mini CLI and only supports workspace_mode=local; "
            "use a dedicated container adapter for a /testbed SWE-bench environment"
        )

    root_ok = resolved_root.is_dir()
    checks.append(
        {
            "name": "workspace_root",
            "status": "passed" if root_ok else "failed",
            "detail": str(resolved_root),
        }
    )
    if not root_ok:
        blockers.append(f"workspace root does not exist: {resolved_root}")

    conflicting_specs = [spec for spec in config_specs if _is_container_swebench_spec(spec)]
    specs_ok = not conflicting_specs
    checks.append(
        {
            "name": "adapter_config_workspace_contract",
            "status": "passed" if specs_ok else "failed",
            "detail": {"conflicting_specs": conflicting_specs},
        }
    )
    if conflicting_specs:
        blockers.append(
            "local execution cannot use mini-SWE configs that bind the environment to /testbed: "
            + ", ".join(conflicting_specs)
        )

    git_root: str | None = None
    git_status = "skipped"
    git_detail: dict[str, Any] = {"required": require_git}
    if root_ok and shutil.which("git"):
        try:
            result = subprocess.run(
                ["git", "-C", str(resolved_root), "rev-parse", "--show-toplevel"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                git_root = str(Path(result.stdout.strip()).resolve())
                git_status = "passed" if Path(git_root) == resolved_root else "failed"
                git_detail.update({"detected_root": git_root, "expected_root": str(resolved_root)})
                if git_status == "failed":
                    blockers.append(
                        f"workspace is nested under git root {git_root}; benchmark patch scope must equal {resolved_root}"
                    )
            else:
                git_status = "failed" if require_git else "warning"
                git_detail["stderr"] = result.stderr.strip()[-1000:]
                if require_git:
                    blockers.append("workspace is not a readable Git repository")
        except (OSError, subprocess.SubprocessError) as exc:
            git_status = "failed" if require_git else "warning"
            git_detail["error"] = f"{type(exc).__name__}: {exc}"
            if require_git:
                blockers.append("Git workspace preflight did not complete")
    elif require_git:
        git_status = "failed"
        blockers.append("git executable is unavailable")
    checks.append({"name": "git_workspace_root", "status": git_status, "detail": git_detail})

    return MiniSwePreflightReport(
        status="failed" if blockers else "passed",
        workspace_mode=workspace_mode,
        workspace_root=str(resolved_root),
        git_root=git_root,
        checks=tuple(checks),
        blocking_reasons=tuple(blockers),
    )


def _is_container_swebench_spec(spec: str) -> bool:
    value = str(spec).strip()
    normalized = value.replace("\\", "/").lower()
    if re.search(r"(^|/)swebench(?:_[^/]*)?\.yaml$", normalized):
        return True
    if normalized.startswith("environment.cwd="):
        return normalized.partition("=")[2].rstrip("/") == "/testbed"
    path = Path(value).expanduser()
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"(?m)^\s*cwd\s*:\s*['\"]?/testbed/?['\"]?\s*$", content))
