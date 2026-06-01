from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .store import ExperienceStore


@dataclass(frozen=True)
class CommandContext:
    """Shared CLI command context.

    New commands should load this once, then call services/engines from the
    command layer instead of reimplementing project initialization logic.
    """

    project_root: Path
    config: Config
    store: ExperienceStore


def load_command_context(project_root: Path, *, initialize: bool = True) -> CommandContext:
    config = Config.load(project_root)
    store = ExperienceStore(config.paths)
    if initialize:
        store.initialize(config)
    return CommandContext(project_root=config.paths.root, config=config, store=store)
