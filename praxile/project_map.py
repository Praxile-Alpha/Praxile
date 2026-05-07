from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import Config
from .constants import DEFAULT_EXCLUDES
from .inspector import inspect_project
from .security import SafetyPolicy
from .utils import utc_now


HIGH_RISK_PATH_TERMS = {
    "auth",
    "session",
    "permission",
    "policy",
    "route",
    "router",
    "schema",
    "migration",
    "database",
    "storage",
    "secret",
}

IMPORTANT_FILES = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "README.md",
    "pytest.ini",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
}

PROJECT_MAP_CACHE_VERSION = 1


def build_project_map(
    config: Config,
    *,
    max_dirs: int = 80,
    max_files: int = 120,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_enabled = bool(config.get("project_map", "cache_enabled", default=True))
    cache_ttl = int(config.get("project_map", "cache_ttl_seconds", default=30) or 0)
    if cache_enabled and not refresh:
        cached = _read_cached_project_map(config, max_dirs=max_dirs, max_files=max_files, ttl_seconds=cache_ttl)
        if cached:
            return cached
    project_map = _scan_project_map(config, max_dirs=max_dirs, max_files=max_files)
    project_map["cache"] = {
        "enabled": cache_enabled,
        "hit": False,
        "ttl_seconds": cache_ttl,
        "path": str(_cache_path(config).relative_to(config.paths.root)),
    }
    if cache_enabled:
        _write_cached_project_map(config, project_map, max_dirs=max_dirs, max_files=max_files)
    return project_map


def _scan_project_map(config: Config, *, max_dirs: int, max_files: int) -> dict[str, Any]:
    root = config.paths.root
    safety = SafetyPolicy(config)
    profile = inspect_project(root)
    directories: dict[str, int] = {}
    root_files: list[str] = []
    important_files: list[str] = []
    high_risk_modules: list[str] = []
    total_files = 0
    protected_skipped = 0

    for current, dirs, filenames in os.walk(root):
        current_path = Path(current)
        rel_dir = "." if current_path == root else current_path.relative_to(root).as_posix()
        rel_parts = set() if rel_dir == "." else set(Path(rel_dir).parts)
        if rel_parts & DEFAULT_EXCLUDES:
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if name not in DEFAULT_EXCLUDES]
        visible_count = 0
        for filename in filenames:
            rel = (current_path / filename).relative_to(root).as_posix()
            decision = safety.check_path(rel)
            if not decision.allowed:
                protected_skipped += 1
                continue
            total_files += 1
            visible_count += 1
            lowered = rel.lower()
            if rel_dir == "." and len(root_files) < max_files:
                root_files.append(rel)
            if filename in IMPORTANT_FILES and len(important_files) < max_files:
                important_files.append(rel)
            if any(term in lowered for term in HIGH_RISK_PATH_TERMS) and len(high_risk_modules) < max_files:
                high_risk_modules.append(rel)
        if visible_count and len(directories) < max_dirs:
            directories[rel_dir] = directories.get(rel_dir, 0) + visible_count

    return {
        "root": str(root),
        "generated_at": utc_now(),
        "profile": profile.to_dict(),
        "total_files": total_files,
        "protected_skipped": protected_skipped,
        "directories": directories,
        "root_files": sorted(root_files),
        "important_files": sorted(set(important_files)),
        "high_risk_modules": sorted(set(high_risk_modules)),
        "truncated": len(directories) >= max_dirs or len(root_files) >= max_files,
    }


def _read_cached_project_map(config: Config, *, max_dirs: int, max_files: int, ttl_seconds: int) -> dict[str, Any] | None:
    path = _cache_path(config)
    if ttl_seconds <= 0 or not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if envelope.get("cache_key") != _cache_key(config, max_dirs=max_dirs, max_files=max_files):
        return None
    created_at = float(envelope.get("created_monotonic", 0) or 0)
    if time.monotonic() - created_at > ttl_seconds:
        return None
    data = envelope.get("project_map")
    if not isinstance(data, dict):
        return None
    data = dict(data)
    data["cache"] = {
        "enabled": True,
        "hit": True,
        "ttl_seconds": ttl_seconds,
        "age_seconds": round(time.monotonic() - created_at, 3),
        "path": str(path.relative_to(config.paths.root)),
    }
    return data


def _write_cached_project_map(config: Config, project_map: dict[str, Any], *, max_dirs: int, max_files: int) -> None:
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cache_key": _cache_key(config, max_dirs=max_dirs, max_files=max_files),
        "created_monotonic": time.monotonic(),
        "project_map": project_map,
    }
    try:
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return


def _cache_key(config: Config, *, max_dirs: int, max_files: int) -> dict[str, Any]:
    return {
        "version": PROJECT_MAP_CACHE_VERSION,
        "root": str(config.paths.root),
        "max_dirs": max_dirs,
        "max_files": max_files,
    }


def _cache_path(config: Config) -> Path:
    return config.paths.state / "cache" / "project_map.json"


def format_project_map(project_map: dict[str, Any]) -> str:
    profile = project_map.get("profile", {})
    lines = [
        f"Project: {project_map.get('root')}",
        f"Stacks: {', '.join(profile.get('stacks') or []) or '(none)'}",
        f"Files: {project_map.get('total_files', 0)} visible, protected skipped={project_map.get('protected_skipped', 0)}",
        f"Cache: {'hit' if project_map.get('cache', {}).get('hit') else 'miss'}",
        "Directories:",
    ]
    directories = project_map.get("directories", {})
    for path, count in sorted(directories.items()):
        lines.append(f"- {path}/  {count} files")
    root_files = project_map.get("root_files", [])
    if root_files:
        lines.append("Root files:")
        lines.extend(f"- {path}" for path in root_files[:40])
    important = project_map.get("important_files", [])
    if important:
        lines.append("Important files:")
        lines.extend(f"- {path}" for path in important[:40])
    high_risk = project_map.get("high_risk_modules", [])
    if high_risk:
        lines.append("High-risk path signals:")
        lines.extend(f"- {path}" for path in high_risk[:40])
    return "\n".join(lines)
