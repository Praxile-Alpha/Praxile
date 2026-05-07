from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .store import ExperienceStore
from .utils import shorten


@dataclass(frozen=True)
class Skill:
    name: str
    path: str
    content: str
    version: str = "unknown"
    status: str = "active"


class SkillSystem:
    """Project-local SKILL.md discovery and loading."""

    def __init__(self, config: Config):
        self.config = config
        self.store = ExperienceStore(config.paths)

    def ensure(self) -> None:
        self.store.initialize(self.config)

    @property
    def root(self) -> Path:
        return self.config.paths.state / "skills"

    def list(self, *, include_inactive: bool = False) -> list[Skill]:
        self.ensure()
        skills: list[Skill] = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            metadata = self.metadata(path.parent.name)
            status = str(metadata.get("status", "active"))
            if status != "active" and not include_inactive:
                self.store.index_asset(path)
                continue
            skills.append(
                Skill(
                    name=path.parent.name,
                    path=str(path.relative_to(self.config.paths.root)),
                    content=path.read_text(encoding="utf-8"),
                    version=str(metadata.get("version", "unknown")),
                    status=status,
                )
            )
        return skills

    def load(self, name: str, *, include_inactive: bool = False) -> Skill:
        self.ensure()
        path = self.root / name / "SKILL.md"
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {name}")
        metadata = self.metadata(name)
        status = str(metadata.get("status", "active"))
        if status != "active" and not include_inactive:
            raise FileNotFoundError(f"Skill is not active: {name} ({status})")
        return Skill(
            name=name,
            path=str(path.relative_to(self.config.paths.root)),
            content=path.read_text(encoding="utf-8"),
            version=str(metadata.get("version", "unknown")),
            status=status,
        )

    def search(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        self.ensure()
        for path in self.root.glob("*/SKILL.md"):
            self.store.index_asset(path)
        return self.store.retrieve(query, kinds=["skill"], limit=limit)

    def metadata(self, name: str) -> dict[str, Any]:
        path = self.root / name / "metadata.json"
        if not path.exists():
            return {"name": name, "status": "active", "version": "legacy"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"name": name, "status": "inactive_metadata_error", "version": "unknown"}
        if not isinstance(data, dict):
            return {"name": name, "status": "inactive_metadata_error", "version": "unknown"}
        data.setdefault("name", name)
        data.setdefault("status", "active")
        data.setdefault("version", "unknown")
        return data

    def history(self, name: str) -> list[dict[str, Any]]:
        self.ensure()
        versions_dir = self.root / name / "versions"
        history: list[dict[str, Any]] = []
        for path in sorted(versions_dir.glob("*.md")):
            history.append(
                {
                    "version": path.stem,
                    "path": str(path.relative_to(self.config.paths.root)),
                    "summary": shorten(path.read_text(encoding="utf-8", errors="replace"), 400),
                }
            )
        return history

    def context_for_task(self, task: str, *, limit: int = 4, max_chars: int = 1200) -> str:
        matches = self.search(task, limit=limit)
        return "\n\n".join(
            f"[skill] {item['path']}\n{shorten(item['snippet'], max_chars)}"
            for item in matches
        )

    def validate_skill_md(self, content: str) -> list[str]:
        issues: list[str] = []
        if not content.lstrip().startswith("# "):
            issues.append("SKILL.md should start with an H1 title.")
        lower = content.lower()
        if "when to use" not in lower and "## when" not in lower:
            issues.append("SKILL.md should describe when to use the skill.")
        if "procedure" not in lower and "steps" not in lower:
            issues.append("SKILL.md should include a procedure or steps section.")
        return issues
