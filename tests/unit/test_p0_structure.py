from __future__ import annotations

from pathlib import Path

import praxile.cli as cli
from praxile.services import (
    AuditService,
    ContextJuiceService,
    GovernanceLoopService,
    GraphService,
    ModelService,
    PolicyService,
    ReflectService,
    RepositoryMemoryTreeService,
    RunService,
    SpecService,
)
from praxile.store import ExperienceStore


def test_public_cli_entry_is_small_compatibility_facade() -> None:
    path = Path(cli.__file__)
    assert path.name == "cli.py"
    assert sum(1 for _ in path.open(encoding="utf-8")) < 300
    legacy_path = path.with_name("cli_legacy.py")
    assert sum(1 for _ in legacy_path.open(encoding="utf-8")) < 50
    assert callable(cli.main)
    assert callable(cli.build_parser)


def test_store_public_entry_remains_compatible() -> None:
    assert ExperienceStore.__name__ == "ExperienceStore"
    assert ExperienceStore.__module__ == "praxile.store.core"
    import praxile.store.assets as assets
    import praxile.store.graph as graph
    import praxile.store.proposals as proposals
    import praxile.store.tasks as tasks
    import praxile.store.trajectories as trajectories

    assert assets.AssetsRepository
    assert graph.GraphRepository
    assert proposals.ProposalsRepository
    assert tasks.TasksRepository
    assert trajectories.TrajectoriesRepository


def test_p0_service_wrappers_are_importable() -> None:
    assert all(
        [
            RunService,
            GraphService,
            AuditService,
            ReflectService,
            SpecService,
            ModelService,
            ContextJuiceService,
            RepositoryMemoryTreeService,
            PolicyService,
            GovernanceLoopService,
        ]
    )
