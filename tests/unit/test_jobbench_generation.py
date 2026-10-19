import hashlib
import json

import pytest

from praxile.eval.jobbench import JobBenchGeneration
from praxile.eval.v2.schema import EvalSchemaError


def fixture_generation(root):
    records = []
    for split in ("main", "easy"):
        task_id = f"{split}--writer--task1"
        task = root / "tasks" / task_id
        (task / "environment/task_folder").mkdir(parents=True)
        (task / "tests").mkdir()
        (task / "task.toml").write_text('[verifier]\nenvironment_mode="separate"\n')
        (task / "instruction.md").write_text("Read task_folder/TASK_INSTRUCTIONS.txt; save to output.")
        (task / "environment/task_folder/TASK_INSTRUCTIONS.txt").write_text("Write a report.")
        (task / "environment/task_folder/reference.txt").write_text("Public facts")
        (task / "tests/RUBRICS.json").write_text('{"rubrics":["PRIVATE_RUBRIC"]}')
        (task / "tests/judge.py").write_text("# official fixture")
        records.append({"id": task_id, "split": split, "revision": "a" * 40, "repo_id": "JobBench/job-bench"})
    manifest = {"schema_version": 1, "repo_id": "JobBench/job-bench", "revision": "a" * 40,
                "task_count": 2, "tasks": records}
    return freeze(root, manifest)


def freeze(root, manifest):
    manifest["generated_files"] = [{"path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted((root / "tasks").rglob("*")) if path.is_file()]
    raw = json.dumps(manifest).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_split_selection_and_private_projection(tmp_path):
    generation = JobBenchGeneration(tmp_path, manifest_sha256=fixture_generation(tmp_path))
    assert len(generation.select()) == 2
    task, = generation.select(split="easy")
    assert task.task_id == "easy--writer--task1"
    view = task.to_adapter_task("/workspace", trace_id="trace", run_id="run")
    public = json.dumps(view.metadata) + view.instruction
    assert "PRIVATE_RUBRIC" not in public
    assert "tests/" not in public
    assert len(task.inputs) == 2
    job = generation.job_config(agent={"name": "fixture"}, jobs_dir=tmp_path / "jobs", job_name="trial", split="main")
    assert len(job["tasks"]) == 1
    with pytest.raises(EvalSchemaError):
        generation.select(split="main", task_ids=(task.task_id,))
    with pytest.raises(EvalSchemaError):
        generation.select(task_ids=("unknown",))


@pytest.mark.parametrize("mutation", ["changed", "extra", "symlink", "private_leak"])
def test_rejects_tampering_and_material_leaks(tmp_path, mutation):
    digest = fixture_generation(tmp_path)
    task = tmp_path / "tasks/main--writer--task1"
    if mutation == "changed":
        (task / "instruction.md").write_text("changed")
    elif mutation == "extra":
        (task / "extra").write_text("unlisted")
    elif mutation == "symlink":
        (task / "environment/link").symlink_to(task / "tests/RUBRICS.json")
    else:
        (task / "environment/leak.json").write_bytes((task / "tests/RUBRICS.json").read_bytes())
        digest = freeze(tmp_path, json.loads((tmp_path / "manifest.json").read_text()))
    with pytest.raises(EvalSchemaError):
        JobBenchGeneration(tmp_path, manifest_sha256=digest)


def test_job_revalidates_generation_before_dispatch(tmp_path):
    generation = JobBenchGeneration(tmp_path, manifest_sha256=fixture_generation(tmp_path))
    (tmp_path / "tasks/main--writer--task1/tests/judge.py").write_text("modified evaluator")
    with pytest.raises(EvalSchemaError, match="digest"):
        generation.job_config(agent={}, jobs_dir=tmp_path / "jobs", job_name="trial")


def test_rejects_shared_verifier_even_with_valid_hash(tmp_path):
    fixture_generation(tmp_path)
    (tmp_path / "tasks/main--writer--task1/task.toml").write_text('[verifier]\nenvironment_mode="same"\n')
    digest = freeze(tmp_path, json.loads((tmp_path / "manifest.json").read_text()))
    with pytest.raises(EvalSchemaError, match="separate"):
        JobBenchGeneration(tmp_path, manifest_sha256=digest)
