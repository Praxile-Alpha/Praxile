from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .schema import EvalSchemaError, EvalTask, EvalTaskSet, RepositorySpec, SWEbenchEvaluationSpec


class SWEbenchTaskLoader:
    """Load an official SWE-bench JSON/JSONL export without exposing gold data to adapters."""

    def __init__(self, *, dataset_name: str = "SWE-bench/SWE-bench_Lite", split: str = "test"):
        self.dataset_name = dataset_name
        self.split = split

    def load(
        self,
        path: Path,
        *,
        name: str | None = None,
        instance_ids: Iterable[str] | None = None,
        development_size: int | None = None,
        seed: str = "praxile-p0",
    ) -> EvalTaskSet:
        rows = self._read_rows(path)
        return self._build_task_set(
            rows,
            source=str(path.resolve()),
            name=name or path.stem,
            instance_ids=instance_ids,
            development_size=development_size,
            seed=seed,
        )

    def load_huggingface(
        self,
        *,
        name: str | None = None,
        instance_ids: Iterable[str] | None = None,
        development_size: int | None = None,
        seed: str = "praxile-p0",
        dataset_loader: Callable[..., Iterable[Mapping[str, Any]]] | None = None,
    ) -> EvalTaskSet:
        if dataset_loader is None:
            try:
                from datasets import load_dataset
            except ImportError as exc:
                raise RuntimeError(
                    "Hugging Face task loading requires the benchmark extra: "
                    "python -m pip install 'praxile[benchmark]'"
                ) from exc
            dataset_loader = load_dataset
        rows = list(dataset_loader(self.dataset_name, split=self.split))
        if any(not isinstance(row, Mapping) for row in rows):
            raise EvalSchemaError("Hugging Face dataset must yield task objects")
        return self._build_task_set(
            rows,
            source=f"huggingface:{self.dataset_name}@{self.split}",
            name=name or self.dataset_name.rsplit("/", 1)[-1],
            instance_ids=instance_ids,
            development_size=development_size,
            seed=seed,
        )

    def _build_task_set(
        self,
        rows: list[Mapping[str, Any]],
        *,
        source: str,
        name: str,
        instance_ids: Iterable[str] | None,
        development_size: int | None,
        seed: str,
    ) -> EvalTaskSet:
        if not rows:
            raise EvalSchemaError("SWE-bench source returned no instances")
        wanted = set(instance_ids or ())
        selection: dict[str, Any] = {"source": source, "mode": "all"}
        if wanted:
            rows = [row for row in rows if str(row.get("instance_id") or "") in wanted]
            missing = sorted(wanted - {str(row.get("instance_id") or "") for row in rows})
            if missing:
                raise EvalSchemaError(f"missing SWE-bench instances: {missing}")
            selection.update({"mode": "instance_ids", "instance_ids": sorted(wanted)})
        if development_size is not None:
            if development_size <= 0:
                raise EvalSchemaError("development_size must be greater than zero")
            rows = sorted(rows, key=lambda row: self._selection_key(str(row.get("instance_id") or ""), seed))[
                :development_size
            ]
            selection.update({"mode": "development_subset", "size": development_size, "seed": seed})
        tasks = tuple(self._parse_row(row) for row in rows)
        return EvalTaskSet(
            name=name,
            dataset_name=self.dataset_name,
            split=self.split,
            tasks=tasks,
            selection=selection,
        )

    def _parse_row(self, row: Mapping[str, Any]) -> EvalTask:
        task_id = str(row.get("instance_id") or "")
        repo = str(row.get("repo") or "")
        if not repo:
            raise EvalSchemaError(f"{task_id or '<unknown>'}: missing repo")
        metadata = {key: row[key] for key in ("version", "created_at") if row.get(key) not in (None, "")}
        private_metadata = {"hints_text": row["hints_text"]} if row.get("hints_text") not in (None, "") else {}
        image_assets = row.get("image_assets") if isinstance(row.get("image_assets"), Mapping) else {}
        return EvalTask(
            task_id=task_id,
            instruction=str(row.get("problem_statement") or ""),
            repository=RepositorySpec(
                repo=repo,
                base_commit=str(row.get("base_commit") or ""),
                clone_url=str(row.get("clone_url") or f"https://github.com/{repo}.git"),
                environment_setup_commit=_optional(row.get("environment_setup_commit")),
            ),
            evaluation=SWEbenchEvaluationSpec(
                dataset_name=self.dataset_name,
                split=self.split,
                fail_to_pass=self._test_list(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS", task_id),
                pass_to_pass=self._test_list(row.get("PASS_TO_PASS"), "PASS_TO_PASS", task_id),
                test_patch=str(row.get("test_patch") or ""),
                reference_patch=str(row.get("patch") or ""),
                image_assets=dict(image_assets),
                private_metadata=private_metadata,
            ),
            metadata=metadata,
        )

    @staticmethod
    def _read_rows(path: Path) -> list[Mapping[str, Any]]:
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".jsonl":
            rows: list[Any] = []
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise EvalSchemaError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise EvalSchemaError(f"{path}: invalid JSON: {exc.msg}") from exc
            rows = payload.get("instances") if isinstance(payload, Mapping) else payload
        if not isinstance(rows, list) or not rows:
            raise EvalSchemaError(f"{path}: expected a non-empty array of SWE-bench instances")
        if any(not isinstance(row, Mapping) for row in rows):
            raise EvalSchemaError(f"{path}: every SWE-bench instance must be an object")
        return list(rows)

    @staticmethod
    def _test_list(value: Any, name: str, task_id: str) -> tuple[str, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise EvalSchemaError(f"{task_id}: {name} must be a JSON array") from exc
        if value is None:
            return ()
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise EvalSchemaError(f"{task_id}: {name} must be an array of non-empty strings")
        return tuple(value)

    @staticmethod
    def _selection_key(task_id: str, seed: str) -> str:
        return hashlib.sha256(f"{seed}\0{task_id}".encode("utf-8")).hexdigest()


def _optional(value: Any) -> str | None:
    return str(value) if value is not None and str(value).strip() else None
