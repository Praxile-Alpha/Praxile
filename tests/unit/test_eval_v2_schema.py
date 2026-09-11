from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from praxile.adapters import AdapterPolicy, FixtureAgentAdapter
from praxile.eval.v2 import (
    EVAL_TASK_SCHEMA_VERSION,
    EvalRunManifest,
    EvalSchemaError,
    EvalTask,
    EvalTaskSet,
    ImmutableManifestStore,
    RepositorySpec,
    SWEbenchEvaluationSpec,
    SWEbenchTaskLoader,
)


def _task(task_id: str = "owner__repo-1") -> EvalTask:
    return EvalTask(
        task_id=task_id,
        instruction="Fix the failing parser",
        repository=RepositorySpec("owner/repo", "a" * 40, "https://github.com/owner/repo.git"),
        evaluation=SWEbenchEvaluationSpec(
            dataset_name="SWE-bench/SWE-bench_Lite",
            split="test",
            fail_to_pass=("tests/test_parser.py::test_bug",),
            test_patch="secret test patch",
            reference_patch="secret gold patch",
        ),
    )


def test_eval_task_round_trip_and_adapter_view_hide_gold_data(tmp_path: Path) -> None:
    base = _task()
    task = EvalTask(
        task_id=base.task_id,
        instruction=base.instruction,
        repository=base.repository,
        evaluation=SWEbenchEvaluationSpec(
            dataset_name=base.evaluation.dataset_name,
            split=base.evaluation.split,
            fail_to_pass=base.evaluation.fail_to_pass,
            test_patch=base.evaluation.test_patch,
            reference_patch=base.evaluation.reference_patch,
            private_metadata={"hints_text": "secret hint"},
        ),
    )

    restored = EvalTask.from_dict(task.to_dict())
    adapter_task = restored.to_adapter_task(str(tmp_path), trace_id="trace-1", run_id="run-1")

    assert restored == task
    assert restored.schema_version == EVAL_TASK_SCHEMA_VERSION
    assert "secret" not in json.dumps(adapter_task.to_dict())
    assert adapter_task.metadata["base_commit"] == "a" * 40


def test_task_fingerprint_includes_private_evaluator_inputs() -> None:
    original = _task()
    changed = EvalTask(
        task_id=original.task_id,
        instruction=original.instruction,
        repository=original.repository,
        evaluation=SWEbenchEvaluationSpec(
            dataset_name=original.evaluation.dataset_name,
            split=original.evaluation.split,
            test_patch="changed test patch",
        ),
    )

    assert original.fingerprint != changed.fingerprint


def test_swebench_loader_supports_jsonl_and_deterministic_development_subset(tmp_path: Path) -> None:
    path = tmp_path / "tasks.jsonl"
    rows = [
        {
            "instance_id": f"owner__repo-{index}",
            "repo": "owner/repo",
            "base_commit": str(index) * 40,
            "problem_statement": f"Fix issue {index}",
            "patch": f"gold-{index}",
            "test_patch": f"test-{index}",
            "FAIL_TO_PASS": json.dumps([f"test_{index}"]),
            "PASS_TO_PASS": [],
        }
        for index in range(1, 5)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    loader = SWEbenchTaskLoader()

    first = loader.load(path, development_size=2, seed="fixed")
    second = loader.load(path, development_size=2, seed="fixed")

    assert [task.task_id for task in first.tasks] == [task.task_id for task in second.tasks]
    assert first.digest == second.digest
    assert first.selection["mode"] == "development_subset"
    assert len(first.tasks) == 2


def test_swebench_loader_rejects_missing_requested_instance(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {
                    "instance_id": "owner__repo-1",
                    "repo": "owner/repo",
                    "base_commit": "a" * 40,
                    "problem_statement": "Fix it",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvalSchemaError, match="missing SWE-bench instances"):
        SWEbenchTaskLoader().load(path, instance_ids=["owner__repo-2"])


def test_swebench_loader_records_explicit_instance_selection(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps(
            [
                {
                    "instance_id": "owner__repo-1",
                    "repo": "owner/repo",
                    "base_commit": "a" * 40,
                    "problem_statement": "Fix it",
                }
            ]
        ),
        encoding="utf-8",
    )

    task_set = SWEbenchTaskLoader().load(path, instance_ids=["owner__repo-1"])

    assert task_set.selection == {
        "source": str(path.resolve()),
        "mode": "instance_ids",
        "instance_ids": ["owner__repo-1"],
    }


def test_swebench_loader_can_use_huggingface_dataset_interface() -> None:
    calls: list[tuple[str, str]] = []

    def fake_load_dataset(name: str, *, split: str):
        calls.append((name, split))
        return [
            {
                "instance_id": "owner__repo-1",
                "repo": "owner/repo",
                "base_commit": "a" * 40,
                "problem_statement": "Fix it",
                "hints_text": "evaluator-owned hint",
            }
        ]

    task_set = SWEbenchTaskLoader(dataset_name="org/dataset", split="dev").load_huggingface(
        dataset_loader=fake_load_dataset
    )

    assert calls == [("org/dataset", "dev")]
    assert task_set.selection["source"] == "huggingface:org/dataset@dev"
    assert task_set.tasks[0].evaluation.private_metadata["hints_text"] == "evaluator-owned hint"
    assert "hints_text" not in task_set.tasks[0].to_adapter_task("/tmp", trace_id="t", run_id="r").metadata


def test_manifest_is_reproducible_redacted_and_write_once(tmp_path: Path) -> None:
    task_set = EvalTaskSet("dev", "SWE-bench/SWE-bench_Lite", "test", (_task(),))
    adapter = FixtureAgentAdapter()
    policy = AdapterPolicy(policy_id="baseline", budgets={"wall_timeout_seconds": 30})
    manifest = EvalRunManifest.create(
        eval_run_id="eval-1",
        task_set=task_set,
        adapter=adapter,
        policy=policy,
        model={"model_name_or_path": "fixture", "api_key": "do-not-store"},
        evaluator={"name": "fixture", "version": "1"},
        execution={"task_concurrency": 1},
    )
    store = ImmutableManifestStore(tmp_path)

    path = store.write_once(manifest)

    assert store.load("eval-1") == manifest
    assert "do-not-store" not in path.read_text(encoding="utf-8")
    assert manifest.reproducibility["model"]["api_key"] == "<redacted>"
    assert store.write_once(manifest) == path


def test_manifest_store_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(EvalSchemaError, match="unsafe eval_run_id"):
        ImmutableManifestStore(tmp_path).path_for("../../escape")
