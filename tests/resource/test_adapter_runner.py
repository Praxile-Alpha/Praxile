from __future__ import annotations

from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy, AdapterRunner, AdapterTask, FixtureAgentAdapter, FixtureArtifact
from praxile.config import Config
from praxile.trace import EventStore


pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


def test_adapter_runner_commits_fixture_evidence_to_event_store(tmp_path: Path) -> None:
    store = EventStore(Config.load(tmp_path).paths)
    runner = AdapterRunner(store)
    adapter = FixtureAgentAdapter(
        artifacts=[FixtureArtifact("patch", "fixture://fix.patch", "sha256:fixture-patch")]
    )

    result = runner.execute(
        adapter,
        AdapterTask("task_runner", "Fix the bug", str(tmp_path)),
        AdapterPolicy(policy_id="baseline"),
    )

    replay = store.replay(result.handle.trace_id)
    assert replay["event_count"] == len(result.events)
    assert replay["artifacts"][0]["artifact_id"] == result.artifacts[0].artifact_id
    assert replay["runs"][0]["status"] == "completed"
