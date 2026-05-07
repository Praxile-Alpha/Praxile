from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .store import MEMORY_FILES, ExperienceStore
from .utils import shorten, utc_now


@dataclass(frozen=True)
class MemoryEntry:
    scope: str
    path: str
    content: str


class MemorySystem:
    """Project-local memory owned by Praxile."""

    def __init__(self, config: Config):
        self.config = config
        self.store = ExperienceStore(config.paths)

    def ensure(self) -> None:
        self.store.initialize(self.config)

    def memory_path(self, scope: str) -> Path:
        if scope not in MEMORY_FILES:
            allowed = ", ".join(sorted(MEMORY_FILES))
            raise ValueError(f"Unknown memory scope: {scope}. Expected one of: {allowed}")
        return self.config.paths.state / "memory" / f"{scope}.md"

    def list(self) -> list[MemoryEntry]:
        self.ensure()
        entries: list[MemoryEntry] = []
        for scope in MEMORY_FILES:
            path = self.memory_path(scope)
            entries.append(
                MemoryEntry(
                    scope=scope,
                    path=str(path.relative_to(self.config.paths.root)),
                    content=path.read_text(encoding="utf-8") if path.exists() else "",
                )
            )
        return entries

    def read(self, scope: str) -> MemoryEntry:
        self.ensure()
        path = self.memory_path(scope)
        return MemoryEntry(
            scope=scope,
            path=str(path.relative_to(self.config.paths.root)),
            content=path.read_text(encoding="utf-8") if path.exists() else "",
        )

    def append(self, scope: str, text: str, *, source: str = "manual") -> MemoryEntry:
        self.ensure()
        path = self.memory_path(scope)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        block = (
            f"\n\n## Update {utc_now()}\n\n"
            f"Source: {source}\n\n"
            f"{text.strip()}\n"
        )
        path.write_text(current.rstrip() + block, encoding="utf-8")
        self.store.index_asset(path)
        return self.read(scope)

    def search(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        self.ensure()
        return self.store.retrieve(query, kinds=["memory"], limit=limit)

    def summarize(self, *, max_chars: int = 4000) -> str:
        parts = []
        for entry in self.list():
            parts.append(f"[{entry.scope}] {entry.path}\n{shorten(entry.content.strip(), max_chars)}")
        return "\n\n".join(parts)
