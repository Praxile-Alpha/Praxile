"""Inventory and freeze official WorkBuddy dataset packages (no task execution)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import tomllib
from pathlib import Path

from ..utils import file_lock
from .contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference
from .jobbench import regular_files
from .v2.schema import EvalSchemaError, canonical_json


class WorkBuddyDataset:
    def __init__(self, root: Path, *, revision: str, expected_digest: str | None = None):
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise EvalSchemaError("WorkBuddy dataset requires an immutable revision")
        self.root = root.resolve(strict=True)
        files = regular_files(self.root)
        hashes = {}
        for name, path in sorted(files.items()):
            with path.open("rb") as handle:
                hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
        self.digest = hashlib.sha256(canonical_json(hashes).encode()).hexdigest()
        if expected_digest is not None and self.digest != expected_digest:
            raise EvalSchemaError("WorkBuddy dataset inventory digest mismatch")
        self.revision = revision
        data = tomllib.loads((self.root / "dataset.toml").read_text())
        identity = data.get("dataset", {})
        match = re.fullmatch(r"wb-bench-(code|web|office|sec)-v1\.0", str(identity.get("id", "")))
        if not match or identity.get("schema") != "workbuddy.dataset.v1":
            raise EvalSchemaError("unsupported WorkBuddy dataset contract")
        self.subset = match.group(1)
        if data.get("layout", {}).get("task_root") != "tasks":
            raise EvalSchemaError("unsupported WorkBuddy task layout")
        verifier = data.get("verifier", {})
        if verifier.get("schema") != "workbuddy.verifier.v1" or verifier.get("engine") != "composite":
            raise EvalSchemaError("official CompositeVerifier contract is required")
        if "shared/verifier/plugin.py" not in files:
            raise EvalSchemaError("official shared verifier plugin is missing")
        self.tasks: dict[str, BenchmarkTask] = {}
        self.preparation_required: list[str] = []
        self.preparation_reasons: dict[str, list[str]] = {}
        task_dirs = sorted((self.root / "tasks").iterdir())
        if len(task_dirs) != identity.get("task_count"):
            raise EvalSchemaError("official task count does not match package")
        for path in task_dirs:
            if not path.is_dir():
                raise EvalSchemaError("unexpected entry in task directory")
            task_id = path.name
            config = tomllib.loads((path / "task.toml").read_text())
            verifier_path = config.get("verifier", {}).get("import_path")
            reasons = []
            if verifier_path is None:
                reasons.append("bind_official_composite_verifier")
            elif verifier_path != "workbuddy_bench.judge:CompositeVerifier":
                raise EvalSchemaError("task does not use official CompositeVerifier")
            if any(config.get(section, {}).get("user") != "dev" for section in ("agent", "verifier")):
                reasons.append("configure_nonroot_task_users")
            if reasons:
                self.preparation_required.append(task_id)
                self.preparation_reasons[task_id] = reasons
            if config.get("task", {}).get("name") != "workbuddy/" + task_id:
                raise EvalSchemaError("task identity mismatch")
            private_hashes = {name: digest for name, digest in hashes.items()
                              if name.startswith("shared/verifier/") or name.startswith(f"tasks/{task_id}/tests/")}
            if not any(name.startswith(f"tasks/{task_id}/tests/") for name in private_hashes):
                raise EvalSchemaError("task verifier materials are missing")
            if f"tasks/{task_id}/environment/workspace.tar.gz" not in files:
                raise EvalSchemaError("task workspace archive is missing")
            self.tasks[task_id] = BenchmarkTask(
                task_id=task_id, benchmark="workbuddy", dataset_revision=revision, split=self.subset,
                instruction=(path / "instruction.md").read_text(encoding="utf-8"),
                environment=EnvironmentSpec("harbor", str(path / "environment"), self.digest),
                evaluator=EvaluatorReference("workbuddy-composite", "1.0", str(path / "tests"),
                    "sha256:" + hashlib.sha256(canonical_json(private_hashes).encode()).hexdigest()),
            )

    def select(self, task_ids: tuple[str, ...] = ()) -> tuple[BenchmarkTask, ...]:
        if len(set(task_ids)) != len(task_ids) or set(task_ids) - self.tasks.keys():
            raise EvalSchemaError("unknown or duplicate requested task")
        return tuple(task for key, task in self.tasks.items() if not task_ids or key in task_ids)


def prepare_copy(source: Path, destination: Path, *, revision: str,
                 expected_digest: str) -> WorkBuddyDataset:
    """Use installed upstream helpers on a verified copy, never on the source."""
    from workbuddy_bench.runner import prepare_tasks

    original = WorkBuddyDataset(source, revision=revision, expected_digest=expected_digest)
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination == original.root or destination.is_relative_to(original.root):
        raise EvalSchemaError("prepared destination must be outside the source dataset")
    helper = Path(prepare_tasks.__file__)
    helper_digest = hashlib.sha256(helper.read_bytes()).hexdigest()
    with file_lock(destination.parent / (destination.name + ".prepare"), timeout_seconds=120):
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("prepared destination already exists")
        with tempfile.TemporaryDirectory(prefix=".workbuddy-prepare-", dir=destination.parent) as stage:
            copied = Path(stage) / "dataset"
            # Preserve links for validation, never follow an unexpected source link.
            shutil.copytree(original.root, copied, symlinks=True)
            WorkBuddyDataset(copied, revision=revision, expected_digest=expected_digest)
            bindings = prepare_tasks.ensure_composite_verifier_contract(copied / "tasks")
            users = prepare_tasks.ensure_task_user(copied / "tasks", agent_user="dev", verifier_user="dev")
            prepared = WorkBuddyDataset(copied, revision=revision)
            if prepared.preparation_required:
                raise EvalSchemaError("official preparation left unresolved task requirements")
            (copied / "praxile-preparation.json").write_text(json.dumps({
                "schema_version": "praxile.workbuddy_preparation.v1",
                "dataset_revision": revision, "source_inventory_sha256": original.digest,
                "helper_sha256": helper_digest, "verifier_bindings_changed": bindings,
                "task_users_changed": users, "agent_user": "dev", "verifier_user": "dev",
            }, indent=2) + "\n", encoding="utf-8")
            copied.rename(destination)
    return WorkBuddyDataset(destination, revision=revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--inventory-sha256")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--prepare-copy", type=Path)
    args = parser.parse_args()
    dataset = WorkBuddyDataset(args.dataset, revision=args.revision, expected_digest=args.inventory_sha256)
    if args.prepare_copy:
        if not args.inventory_sha256:
            parser.error("--prepare-copy requires --inventory-sha256")
        dataset = prepare_copy(args.dataset, args.prepare_copy, revision=args.revision,
                               expected_digest=args.inventory_sha256)
    selected = dataset.select(tuple(args.task_id))
    print(json.dumps({"subset": dataset.subset, "dataset_revision": dataset.revision,
        "inventory_sha256": dataset.digest, "total_tasks": len(dataset.tasks),
        "selected_tasks": len(selected), "requires_official_preparation": dataset.preparation_required,
        "preparation_reasons": dataset.preparation_reasons,
        "tasks": [task.task_id for task in selected]}, indent=2))


if __name__ == "__main__":
    main()
