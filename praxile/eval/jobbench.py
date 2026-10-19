"""Read pinned official JobBench Harbor generations without importing its judge."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
import re
import stat
import tomllib
from pathlib import Path
from typing import Any

from .contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, _relative
from .v2.schema import EvalSchemaError, canonical_json


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_files(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise EvalSchemaError("generation tree must be a real directory")
    files: dict[str, Path] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(entry)
            elif stat.S_ISREG(mode):
                files[entry.relative_to(root).as_posix()] = entry
            else:
                raise EvalSchemaError("generation contains a symlink or special file")
    return files


class JobBenchGeneration:
    """Control-plane view; its manifest must never be passed to the agent."""

    def __init__(self, root: Path, *, manifest_sha256: str):
        self.root = root.resolve(strict=True)
        manifest_path = self.root / "manifest.json"
        if manifest_path.is_symlink():
            raise EvalSchemaError("manifest must not be a symlink")
        raw = manifest_path.read_bytes()
        if sha256(raw) != manifest_sha256:
            raise EvalSchemaError("generation manifest digest mismatch")
        self.manifest_sha256 = manifest_sha256
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise EvalSchemaError("unsupported JobBench generation schema")
        if manifest.get("repo_id") != "JobBench/job-bench":
            raise EvalSchemaError("unexpected JobBench dataset")
        if not re.fullmatch(r"[0-9a-f]{40,64}", str(manifest.get("revision", ""))):
            raise EvalSchemaError("dataset revision must be an immutable commit")
        entries = manifest.get("generated_files")
        if not isinstance(entries, list) or not entries:
            raise EvalSchemaError("generated file inventory is missing")
        actual = {"tasks/" + name: path for name, path in regular_files(self.root / "tasks").items()}
        expected = {}
        for item in entries:
            if not isinstance(item, dict):
                raise EvalSchemaError("invalid generated file record")
            relative = item.get("path")
            _relative(relative)
            if relative in expected:
                raise EvalSchemaError("duplicate generated file")
            expected[relative] = item.get("sha256")
        if set(actual) != set(expected):
            raise EvalSchemaError("generated file inventory mismatch")
        for relative, path in actual.items():
            if sha256(path.read_bytes()) != expected[relative]:
                raise EvalSchemaError(f"generated file digest mismatch: {relative}")
        records = manifest.get("tasks")
        if not isinstance(records, list) or len(records) != manifest.get("task_count") or not records:
            raise EvalSchemaError("task inventory mismatch")
        self.tasks: dict[str, BenchmarkTask] = {}
        self.paths: dict[str, Path] = {}
        for record in records:
            if not isinstance(record, dict):
                raise EvalSchemaError("task record must be an object")
            task_id = record.get("id")
            if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", task_id):
                raise EvalSchemaError("invalid task identifier")
            if task_id in self.tasks or record.get("split") not in {"main", "easy"}:
                raise EvalSchemaError("duplicate task or invalid split")
            if record.get("revision") != manifest["revision"] or record.get("repo_id") != manifest["repo_id"]:
                raise EvalSchemaError("task provenance differs from generation")
            task_path = self.root / "tasks" / task_id
            config = tomllib.loads((task_path / "task.toml").read_text(encoding="utf-8"))
            if config.get("verifier", {}).get("environment_mode") != "separate":
                raise EvalSchemaError("JobBench verifier must use a separate environment")
            prefix = f"tasks/{task_id}/"
            private = {name[len(prefix):]: digest for name, digest in expected.items()
                       if name.startswith(prefix + "tests/")}
            if "tests/RUBRICS.json" not in private or "tests/judge.py" not in private:
                raise EvalSchemaError("official evaluator materials are missing")
            rubric_digest = private["tests/RUBRICS.json"]
            if any(digest == rubric_digest for name, digest in expected.items()
                   if name.startswith(prefix + "environment/")):
                raise EvalSchemaError("private rubric was copied into the agent environment")
            input_prefix = prefix + "environment/task_folder/"
            inputs = tuple(InputArtifact(
                "task_folder/" + name[len(input_prefix):], str(actual[name]), "sha256:" + expected[name],
            ) for name in sorted(actual) if name.startswith(input_prefix))
            if not any(item.path == "task_folder/TASK_INSTRUCTIONS.txt" for item in inputs):
                raise EvalSchemaError("public task instructions are missing")
            self.tasks[task_id] = BenchmarkTask(
                task_id=task_id, benchmark="jobbench", dataset_revision=manifest["revision"],
                split=record["split"], instruction=(task_path / "instruction.md").read_text(encoding="utf-8"),
                environment=EnvironmentSpec("harbor", str(task_path / "environment"), manifest_sha256),
                evaluator=EvaluatorReference("jobbench-official", "harbor-0.22.0", str(task_path / "tests"),
                                             "sha256:" + sha256(canonical_json(private).encode())),
                inputs=inputs,
            )
            self.paths[task_id] = task_path
        actual_ids = {Path(name).parts[1] for name in actual}
        if actual_ids != set(self.tasks):
            raise EvalSchemaError("unlisted task directories")

    def select(self, *, split: str = "all", task_ids: tuple[str, ...] = ()) -> tuple[BenchmarkTask, ...]:
        if split not in {"main", "easy", "all"}:
            raise EvalSchemaError("split must be main, easy, or all")
        if len(set(task_ids)) != len(task_ids) or set(task_ids) - self.tasks.keys():
            raise EvalSchemaError("duplicate or unknown requested task IDs")
        selected = tuple(task for key, task in sorted(self.tasks.items())
                         if (not task_ids or key in task_ids) and (split == "all" or task.split == split))
        if not selected or (task_ids and len(selected) != len(task_ids)):
            raise EvalSchemaError("requested selection is empty or conflicts with split")
        return selected

    def job_config(self, *, agent: dict[str, Any], jobs_dir: Path, job_name: str,
                   split: str = "all", task_ids: tuple[str, ...] = ()) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", job_name):
            raise EvalSchemaError("unsafe job name")
        if (jobs_dir / job_name).exists():
            raise EvalSchemaError("job already exists; use upstream explicit resume")
        JobBenchGeneration(self.root, manifest_sha256=self.manifest_sha256)
        selected = self.select(split=split, task_ids=task_ids)
        return {"job_name": job_name, "jobs_dir": str(jobs_dir.resolve()), "agents": [agent],
                "tasks": [{"path": str(self.paths[task.task_id])} for task in selected], "datasets": []}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--split", choices=["main", "easy", "all"], default="all")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--agent-config", type=Path, help="Harbor agent JSON object; enables job config generation")
    parser.add_argument("--job-name")
    parser.add_argument("--jobs-dir", type=Path)
    parser.add_argument("--write-job", type=Path, help="new private Harbor job JSON; does not execute it")
    args = parser.parse_args()
    generation = JobBenchGeneration(args.generation, manifest_sha256=args.manifest_sha256)
    tasks = generation.select(split=args.split, task_ids=tuple(args.task_id))
    if any((args.agent_config, args.job_name, args.jobs_dir, args.write_job)):
        if not all((args.agent_config, args.job_name, args.jobs_dir, args.write_job)):
            parser.error("job generation requires --agent-config, --job-name, --jobs-dir and --write-job")
        if version("harbor") != "0.22.0":
            parser.error("JobBench job generation requires isolated Harbor 0.22.0")
        from harbor.models.job.config import JobConfig
        agent = json.loads(args.agent_config.read_text(encoding="utf-8"))
        if not isinstance(agent, dict):
            parser.error("agent config must be a JSON object")
        config = generation.job_config(agent=agent, jobs_dir=args.jobs_dir, job_name=args.job_name,
                                       split=args.split, task_ids=tuple(args.task_id))
        JobConfig.model_validate(config)
        # Exclusive creation avoids replacing an experiment definition silently.
        import os
        with os.fdopen(os.open(args.write_job, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump(config, handle, indent=2)
    print(json.dumps({"manifest_sha256": generation.manifest_sha256,
                      "total_tasks": len(generation.tasks), "selected_tasks": len(tasks),
                      "tasks": [{"id": task.task_id, "split": task.split, "fingerprint": task.fingerprint}
                                for task in tasks]}, indent=2))


if __name__ == "__main__":
    main()
