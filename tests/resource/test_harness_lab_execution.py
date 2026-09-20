from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter, FixtureArtifact, FixtureEvent
from praxile.config import Config
from praxile.control_plane import EvidenceRef, ExecutableHarnessLab, ExecutableHarnessManifest, HarnessCandidate, HarnessMechanism
from praxile.eval.v2 import EvalTask, EvalTaskSet, EvaluatorResult, RepositorySpec, SWEbenchEvaluationSpec, SWEbenchPrediction
from praxile.trace import EventStore
from praxile.utils import utc_now


pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


class PassingEvaluator:
    name = "fixture-evaluator"
    version = "1"

    def identity(self) -> Mapping[str, Any]:
        return {"name": self.name, "version": self.version}

    def evaluate(self, task: EvalTask, prediction: SWEbenchPrediction, output_root: Path) -> EvaluatorResult:
        output_root.mkdir(parents=True, exist_ok=True)
        evidence = output_root / "result.json"
        evidence.write_text('{"resolved":true}\n', encoding="utf-8")
        now = utc_now()
        return EvaluatorResult("completed", True, now, now, (str(evidence),), {})


def _repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Praxile Tests"], cwd=path, check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "candidate.patch").write_text("diff --git a/app.py b/app.py\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def _task(task_id: str, commit: str) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        instruction="Fix the fixture",
        repository=RepositorySpec("owner/repo", commit, "https://invalid.example/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec("fixture", "test"),
    )


def test_harness_lab_executes_repeated_isolated_dev_and_heldout_arms(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = _repo(source)
    dev = EvalTaskSet("dev", "fixture", "test", (_task("dev-task", commit),))
    heldout = EvalTaskSet("heldout", "fixture", "test", (_task("heldout-task", commit),))
    digest = "sha256:" + hashlib.sha256((source / "candidate.patch").read_bytes()).hexdigest()
    adapter = FixtureAgentAdapter(
        events=[
            FixtureEvent("TOOL_CALL", payload={"tool": "edit"}),
            FixtureEvent("SKILL_REFERENCE", payload={"skill_id": "focused"}),
            FixtureEvent("VERIFICATION", payload={"status": "passed"}),
        ],
        artifacts=[FixtureArtifact("workspace_patch", "candidate.patch", digest)],
    )
    candidate = HarnessCandidate(
        candidate_id="candidate-lab",
        type="context_policy",
        component_key="context.focused",
        base_version="1",
        candidate_version="2",
        hypothesis="Focused context improves fixture tasks.",
        source_evidence=(EvidenceRef("eval", "source-eval"),),
        payload={"context": "focus"},
        executor_profile="fixture.local",
        task_family="fixture-bugfix",
    )
    evaluator = PassingEvaluator()
    manifest = ExecutableHarnessManifest(
        lab_id="fixture-harness-lab",
        version="1",
        experiment_mode="creation",
        isolation_mode="worktree",
        candidate=candidate,
        adapter_name="fixture",
        executor_config={},
        model={"model_name_or_path": "fixture-model"},
        evaluator=evaluator.identity(),
        baseline_policy=AdapterPolicy(policy_id="baseline", version="1"),
        candidate_policy=AdapterPolicy(policy_id="candidate", version="2", context=({"content": "focus"},)),
        development_task_ids=("dev-task",),
        heldout_task_ids=("heldout-task",),
        development_task_digest=dev.digest,
        heldout_task_digest=heldout.digest,
        repetitions=2,
        mechanisms=tuple(HarnessMechanism(f"mechanism-{coverage}", coverage) for coverage in "ETCSLV"),
    )
    config = Config.load(tmp_path / "control")
    report = ExecutableHarnessLab(config.paths.state, EventStore(config.paths)).run(
        manifest,
        dev,
        heldout,
        adapter=adapter,
        evaluator=evaluator,
        source_overrides={"owner/repo": source},
    )

    assert len(report["arms"]) == 4
    assert report["repetitions"] == 2
    assert all(report["runtime_coverage"]["covered"].values())
    assert report["dead_mechanisms"] == []
    assert report["splits"]["heldout"]["sample_count"] == 2
    assert report["splits"]["heldout"]["delta"]["estimate"] == 0.0
    assert report["promotion_eligible"] is False
    assert (config.paths.state / "eval" / "v2" / "harness-lab" / manifest.lab_id / "manifest.json").is_file()
