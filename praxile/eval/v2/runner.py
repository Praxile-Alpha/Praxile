from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from ...adapters import AdapterPolicy, AdapterRunner, AgentAdapterV2
from ...trace import ArtifactRecord, EventStore
from ...utils import new_id, path_is_relative_to, read_json, utc_now, write_json
from .evaluator import TaskEvaluator, prediction_from_adapter_result
from .manifest import EvalRunManifest, ImmutableManifestStore
from .metrics import aggregate_metrics, trace_metrics
from .repository import BenchmarkRepositoryPreparer
from .schema import EVAL_RESULT_SCHEMA_VERSION, EvalSchemaError, EvalTask, EvalTaskSet


class BenchmarkEvalRunner:
    """Run external coding agents against isolated benchmark tasks with durable results."""

    def __init__(self, state_root: Path, event_store: EventStore):
        self.state_root = state_root.resolve()
        self.event_store = event_store
        self.manifests = ImmutableManifestStore(self.state_root)
        self.repositories = BenchmarkRepositoryPreparer(self.state_root)

    def run(
        self,
        task_set: EvalTaskSet,
        *,
        adapter: AgentAdapterV2,
        evaluator: TaskEvaluator,
        policy: AdapterPolicy,
        model: Mapping[str, Any],
        eval_run_id: str | None = None,
        resume: bool = False,
        keep_workspaces: bool = False,
        source_overrides: Mapping[str, Path] | None = None,
        experiment_variable: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = eval_run_id or new_id("benchmark")
        model_name = str(model.get("model_name_or_path") or model.get("model") or "").strip()
        if not model_name:
            raise EvalSchemaError("model identity requires model_name_or_path or model")
        execution = {
            "resumable": True,
            "keep_workspaces": bool(keep_workspaces),
            "task_concurrency": 1,
            "timeout_seconds": policy.budgets.get("wall_timeout_seconds"),
            "retry_policy": policy.settings.get("retry_policy", {}),
            "seed": policy.settings.get("seed"),
            "source_overrides": {
                repo: str(path.expanduser().resolve()) for repo, path in sorted((source_overrides or {}).items())
            },
        }
        candidate = EvalRunManifest.create(
            eval_run_id=run_id,
            task_set=task_set,
            adapter=adapter,
            policy=policy,
            model=model,
            evaluator=evaluator.identity(),
            execution=execution,
            experiment_variable=experiment_variable,
        )
        manifest_path = self.manifests.path_for(run_id)
        if manifest_path.exists():
            if not resume:
                raise EvalSchemaError(f"eval run already exists; pass resume=True to continue: {run_id}")
            manifest = self.manifests.load(run_id)
            if (
                manifest.reproducibility_digest != candidate.reproducibility_digest
                or dict(manifest.experiment_variable) != dict(candidate.experiment_variable)
            ):
                raise EvalSchemaError("cannot resume an eval run with changed reproducibility inputs")
        else:
            manifest = candidate
            self.manifests.write_once(manifest)

        results: list[dict[str, Any]] = []
        for task in task_set.tasks:
            result_path = self._task_result_path(run_id, task.task_id)
            if resume and result_path.is_file():
                current = read_json(result_path, {})
                if (
                    isinstance(current, dict)
                    and current.get("manifest_digest") == manifest.reproducibility_digest
                    and current.get("status") == "completed"
                ):
                    results.append(current)
                    continue
            source_override = (source_overrides or {}).get(task.repository.repo)
            result = self._run_task(
                task,
                eval_run_id=run_id,
                adapter=adapter,
                evaluator=evaluator,
                policy=policy,
                model_name=model_name,
                manifest_digest=manifest.reproducibility_digest,
                keep_workspace=keep_workspaces,
                source_override=source_override,
            )
            write_json(result_path, result)
            results.append(result)

        report = {
            "schema_version": EVAL_RESULT_SCHEMA_VERSION,
            "report_type": "baseline" if not experiment_variable else "experiment",
            "eval_run_id": run_id,
            "created_at": utc_now(),
            "manifest_path": str(manifest_path),
            "manifest_digest": manifest.reproducibility_digest,
            "task_set": task_set.name,
            "adapter": adapter.name,
            "model_name_or_path": model_name,
            "metrics": aggregate_metrics(results),
            "tasks": results,
        }
        write_json(self._run_root(run_id) / "report.json", report)
        return report

    def _run_task(
        self,
        task: EvalTask,
        *,
        eval_run_id: str,
        adapter: AgentAdapterV2,
        evaluator: TaskEvaluator,
        policy: AdapterPolicy,
        model_name: str,
        manifest_digest: str,
        keep_workspace: bool,
        source_override: Path | None,
    ) -> dict[str, Any]:
        started_at = utc_now()
        started = time.monotonic()
        prepared = None
        adapter_result = None
        prediction = None
        prediction_path = None
        preserved_artifacts: list[ArtifactRecord] = []
        try:
            prepared = self.repositories.prepare(
                task, eval_run_id=eval_run_id, source_override=source_override
            )
            trace_id = new_id("trace")
            agent_run_id = new_id("run")
            adapter_task = task.to_adapter_task(
                str(prepared.workspace_root), trace_id=trace_id, run_id=agent_run_id
            )
            adapter_result = AdapterRunner(self.event_store).execute(adapter, adapter_task, policy)
            task_root = self._task_root(eval_run_id, task.task_id)
            preserved_artifacts = self._materialize_artifacts(
                adapter_result.artifacts,
                workspace_root=prepared.workspace_root,
                output_root=task_root / "agent-artifacts",
                eval_run_id=eval_run_id,
                task_id=task.task_id,
            )
            prediction = prediction_from_adapter_result(
                task,
                adapter_result,
                workspace_root=prepared.workspace_root,
                model_name_or_path=model_name,
            )
            prediction_path = prediction.write_jsonl(task_root / "prediction.jsonl")
            evaluator_result = evaluator.evaluate(task, prediction, task_root / "evaluator")
            elapsed_ms = round((time.monotonic() - started) * 1000)
            metrics = trace_metrics(
                adapter_result.events,
                wall_latency_ms=elapsed_ms,
                resolved=evaluator_result.resolved,
            )
            return {
                "schema_version": EVAL_RESULT_SCHEMA_VERSION,
                "eval_run_id": eval_run_id,
                "task_id": task.task_id,
                "task_fingerprint": task.fingerprint,
                "manifest_digest": manifest_digest,
                "status": "completed" if evaluator_result.status == "completed" else evaluator_result.status,
                "started_at": started_at,
                "ended_at": utc_now(),
                "repository": prepared.to_dict(),
                "trace_id": adapter_result.handle.trace_id,
                "agent_run_id": adapter_result.handle.run_id,
                "prediction_path": str(prediction_path),
                "prediction_digest": "sha256:" + hashlib.sha256(prediction.model_patch.encode("utf-8")).hexdigest(),
                "artifacts": [artifact.to_dict() for artifact in preserved_artifacts],
                "evaluator": evaluator_result.to_dict(),
                "metrics": metrics,
                "error": None,
            }
        except Exception as exc:
            return {
                "schema_version": EVAL_RESULT_SCHEMA_VERSION,
                "eval_run_id": eval_run_id,
                "task_id": task.task_id,
                "task_fingerprint": task.fingerprint,
                "manifest_digest": manifest_digest,
                "status": "error",
                "started_at": started_at,
                "ended_at": utc_now(),
                "repository": prepared.to_dict() if prepared else None,
                "trace_id": adapter_result.handle.trace_id if adapter_result else None,
                "agent_run_id": adapter_result.handle.run_id if adapter_result else None,
                "prediction_path": str(prediction_path) if prediction_path else None,
                "prediction_digest": (
                    "sha256:" + hashlib.sha256(prediction.model_patch.encode("utf-8")).hexdigest()
                    if prediction
                    else None
                ),
                "artifacts": [artifact.to_dict() for artifact in preserved_artifacts],
                "evaluator": {"status": "not_run", "resolved": None},
                "metrics": trace_metrics(
                    adapter_result.events if adapter_result else [],
                    wall_latency_ms=round((time.monotonic() - started) * 1000),
                    resolved=None,
                ),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        finally:
            if prepared and not keep_workspace:
                try:
                    self.repositories.cleanup(prepared)
                except Exception:
                    # The task result remains durable; stale worktrees can be pruned independently.
                    pass

    def _run_root(self, eval_run_id: str) -> Path:
        return self.state_root / "eval" / "v2" / "runs" / _component(eval_run_id)

    def _task_root(self, eval_run_id: str, task_id: str) -> Path:
        return self._run_root(eval_run_id) / "tasks" / _component(task_id)

    def _task_result_path(self, eval_run_id: str, task_id: str) -> Path:
        return self._task_root(eval_run_id, task_id) / "result.json"

    def _materialize_artifacts(
        self,
        artifacts: tuple[ArtifactRecord, ...],
        *,
        workspace_root: Path,
        output_root: Path,
        eval_run_id: str,
        task_id: str,
    ) -> list[ArtifactRecord]:
        preserved: list[ArtifactRecord] = []
        for artifact in artifacts:
            source = _local_artifact_path(artifact.uri, workspace_root)
            if source is None:
                continue
            if not source.is_file() or not path_is_relative_to(source, workspace_root):
                raise EvalSchemaError(f"artifact escapes or is missing from the isolated workspace: {artifact.uri}")
            digest = _file_digest(source)
            if artifact.content_digest.startswith("sha256:") and artifact.content_digest != digest:
                raise EvalSchemaError(f"artifact digest mismatch during materialization: {artifact.artifact_id}")
            artifact_dir = output_root / hashlib.sha256(artifact.artifact_id.encode("utf-8")).hexdigest()[:20]
            artifact_dir.mkdir(parents=True, exist_ok=True)
            target = artifact_dir / source.name
            temporary = target.with_suffix(target.suffix + ".tmp")
            shutil.copy2(source, temporary)
            temporary.replace(target)
            record = ArtifactRecord(
                artifact_id=new_id("eval-artifact"),
                trace_id=artifact.trace_id,
                run_id=artifact.run_id,
                type=artifact.type,
                uri=target.resolve().as_uri(),
                content_digest=digest,
                producer_event_id=artifact.producer_event_id,
                created_at=artifact.created_at,
                media_type=artifact.media_type,
                size=target.stat().st_size,
                metadata={
                    **dict(artifact.metadata),
                    "materialized_by": "praxile.eval.v2",
                    "source_artifact_id": artifact.artifact_id,
                    "eval_run_id": eval_run_id,
                    "task_id": task_id,
                },
            )
            self.event_store.record_artifact(record)
            preserved.append(record)
        return preserved


def _component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", value):
        raise EvalSchemaError(f"unsafe eval identifier: {value!r}")
    return value


def _local_artifact_path(uri: str, workspace_root: Path) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve()
    if parsed.scheme:
        return None
    return (workspace_root / uri).resolve()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
