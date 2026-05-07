from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProjectProfile:
    root: Path
    stacks: list[str]
    markers: list[str]
    package_manager: str | None
    test_commands: list[str]
    required_tools: list[str]
    missing_tools: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "stacks": self.stacks,
            "markers": self.markers,
            "package_manager": self.package_manager,
            "test_commands": self.test_commands,
            "required_tools": self.required_tools,
            "missing_tools": self.missing_tools,
            "notes": self.notes,
        }


def inspect_project(root: Path) -> ProjectProfile:
    root = root.resolve()
    stacks: list[str] = []
    markers: list[str] = []
    commands: list[str] = []
    notes: list[str] = []
    package_manager: str | None = None

    package_json = _read_package_json(root)
    if package_json is not None:
        _append_unique(stacks, "node")
        markers.append("package.json")
        package_manager = _detect_package_manager(root)
        _inspect_node(package_json, package_manager, stacks, commands, notes)

    if _has_any(root, ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"]):
        _append_unique(stacks, "python")
        markers.extend(_existing(root, ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini"]))
        _inspect_python(root, commands, notes)
    elif (root / "tests").exists() and list(root.glob("*.py")):
        _append_unique(stacks, "python")
        markers.append("tests/")
        _append_unique(commands, "python -m unittest")

    if (root / "go.mod").exists():
        _append_unique(stacks, "go")
        markers.append("go.mod")
        _append_unique(commands, "go test ./...")

    if (root / "Cargo.toml").exists():
        _append_unique(stacks, "rust")
        markers.append("Cargo.toml")
        _append_unique(commands, "cargo test")

    required_tools = _required_tools_for(commands)
    missing_tools = [tool for tool in required_tools if not _tool_available(tool)]
    if not stacks:
        notes.append("No common stack markers detected; configure runtime.default_test_commands manually if needed.")
    if commands and missing_tools:
        notes.append("Some detected verification commands require tools that are not on PATH.")

    return ProjectProfile(
        root=root,
        stacks=stacks,
        markers=sorted(set(markers)),
        package_manager=package_manager,
        test_commands=commands,
        required_tools=required_tools,
        missing_tools=missing_tools,
        notes=notes,
    )


def _read_package_json(root: Path) -> dict[str, Any] | None:
    path = root / "package.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _inspect_node(
    package_json: dict[str, Any],
    package_manager: str | None,
    stacks: list[str],
    commands: list[str],
    notes: list[str],
) -> None:
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    dependencies = _combined_dependencies(package_json)
    lower_deps = {name.lower() for name in dependencies}
    if {"react", "react-dom"} & lower_deps:
        _append_unique(stacks, "react")
    if "next" in lower_deps:
        _append_unique(stacks, "nextjs")
    if "vite" in lower_deps or "@vitejs/plugin-react" in lower_deps:
        _append_unique(stacks, "vite")
    if "typescript" in lower_deps:
        _append_unique(stacks, "typescript")
    if "@playwright/test" in lower_deps or "playwright" in lower_deps:
        _append_unique(stacks, "playwright")
        notes.append("Playwright detected; UI-sensitive tasks should still use human acceptance for visual quality.")

    if "test" in scripts:
        _append_unique(commands, _node_command(package_manager, "test"))
    if "lint" in scripts:
        _append_unique(commands, _node_command(package_manager, "lint"))
    if "build" in scripts:
        _append_unique(commands, _node_command(package_manager, "build"))


def _inspect_python(root: Path, commands: list[str], notes: list[str]) -> None:
    text_blobs: list[str] = []
    for name in ["pyproject.toml", "requirements.txt", "setup.cfg", "pytest.ini"]:
        path = root / name
        if path.exists():
            text_blobs.append(path.read_text(encoding="utf-8", errors="ignore").lower())
    combined = "\n".join(text_blobs)
    if "pytest" in combined or (root / "tests").exists():
        if "slow:" in combined and "integration:" in combined:
            _append_unique(commands, 'python -m pytest -q -m "not slow and not integration"')
            notes.append("Pytest slow/integration markers detected; default verification excludes them for the fast loop.")
        else:
            _append_unique(commands, "python -m pytest")
    else:
        _append_unique(commands, "python -m unittest")
        notes.append("Python detected without pytest markers; using unittest as the conservative default.")


def _detect_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def _node_command(package_manager: str | None, script: str) -> str:
    manager = package_manager or "npm"
    if manager == "pnpm":
        return "pnpm test" if script == "test" else f"pnpm run {script}"
    if manager == "yarn":
        return f"yarn {script}"
    if manager == "bun":
        return "bun test" if script == "test" else f"bun run {script}"
    return "npm test" if script == "test" else f"npm run {script}"


def _combined_dependencies(package_json: dict[str, Any]) -> dict[str, Any]:
    deps: dict[str, Any] = {}
    for key in ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]:
        value = package_json.get(key)
        if isinstance(value, dict):
            deps.update(value)
    return deps


def _required_tools_for(commands: list[str]) -> list[str]:
    tools: list[str] = []
    for command in commands:
        first = command.split(maxsplit=1)[0] if command else ""
        if first:
            _append_unique(tools, first)
    return tools


def _tool_available(tool: str) -> bool:
    if tool == "python":
        return bool(shutil.which("python") or shutil.which("python3"))
    return bool(shutil.which(tool))


def _has_any(root: Path, names: list[str]) -> bool:
    return any((root / name).exists() for name in names)


def _existing(root: Path, names: list[str]) -> list[str]:
    return [name for name in names if (root / name).exists()]


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
