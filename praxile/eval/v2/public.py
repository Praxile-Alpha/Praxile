from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from ...trace import AgentEvent, EventStore
from ...utils import read_json, write_json
from .manifest import ImmutableManifestStore
from .schema import EvalSchemaError, canonical_json


PUBLIC_MANIFEST_SCHEMA_VERSION = "praxile.public_experiment_manifest.v1"
PUBLIC_METRICS_SCHEMA_VERSION = "praxile.public_experiment_metrics.v1"
_PUBLIC_EVENT_TYPES = {
    "RUN_START",
    "CONTEXT_INJECT",
    "ARTIFACT_CHANGE",
    "VERIFICATION",
    "FINAL_RESULT",
    "RUN_END",
}
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret")
_SECRET_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "api_token",
    "auth_token",
    "bearer_token",
    "bot_token",
}
_CONTENT_KEYS = {"instruction", "prompt", "system_prompt", "content"}


class PublicExperimentExporter:
    """Export a bounded, redacted evidence package from one persisted A/B run."""

    def __init__(self, state_root: Path, event_store: EventStore):
        self.state_root = state_root.resolve()
        self.event_store = event_store
        self.manifests = ImmutableManifestStore(self.state_root)

    def export(self, experiment_id: str, output_root: Path) -> dict[str, str]:
        _safe_id(experiment_id)
        experiment_root = self.state_root / "eval" / "v2" / "experiments" / experiment_id
        experiment = _object(read_json(experiment_root / "manifest.json", None), "experiment manifest")
        report = _object(read_json(experiment_root / "report.json", None), "experiment report")
        baseline_id = str(experiment.get("baseline_run_id") or "")
        candidate_id = str(experiment.get("candidate_run_id") or "")
        if not baseline_id or not candidate_id:
            raise EvalSchemaError("public export requires baseline and candidate run IDs")

        baseline_manifest = self.manifests.load(baseline_id)
        candidate_manifest = self.manifests.load(candidate_id)
        baseline_report = self._run_report(baseline_id)
        candidate_report = self._run_report(candidate_id)
        destination = output_root.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)

        public_manifest = {
            "schema_version": PUBLIC_MANIFEST_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "recorded_at": experiment.get("created_at"),
            "track": experiment.get("track"),
            "changed_variable": experiment.get("changed_variable"),
            "frozen_invariants": list(experiment.get("frozen_invariants") or []),
            "task_set_digest": experiment.get("task_set_digest"),
            "candidate": _redact(experiment.get("candidate") or {}, redact_content=True),
            "candidate_digest": experiment.get("candidate_digest"),
            "arms": {
                "baseline": _public_manifest_arm(baseline_manifest.to_dict()),
                "candidate": _public_manifest_arm(candidate_manifest.to_dict()),
            },
            "redaction": {
                "absolute_paths": True,
                "credentials": True,
                "task_instruction": True,
                "context_content": True,
                "native_payload_refs": True,
            },
        }
        public_metrics = {
            "schema_version": PUBLIC_METRICS_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "recorded_at": report.get("created_at"),
            "invariant_check": _redact(report.get("invariant_check") or {}),
            "baseline": _public_run_metrics(
                baseline_report, report.get("diagnoses", {}).get("baseline", [])
            ),
            "candidate": _public_run_metrics(
                candidate_report, report.get("diagnoses", {}).get("candidate", [])
            ),
            "comparison": _redact(report.get("comparison") or {}),
        }
        manifest_path = destination / "experiment-manifest.json"
        metrics_path = destination / "raw-metrics.json"
        trace_path = destination / "trace-sample.jsonl"
        write_json(manifest_path, public_manifest)
        write_json(metrics_path, public_metrics)
        trace_path.write_text(
            self._trace_sample(baseline_report, candidate_report), encoding="utf-8"
        )
        return {
            "manifest": str(manifest_path),
            "metrics": str(metrics_path),
            "trace_sample": str(trace_path),
        }

    def _run_report(self, run_id: str) -> Mapping[str, Any]:
        return _object(
            read_json(self.manifests.path_for(run_id).parent / "report.json", None),
            f"run report {run_id}",
        )

    def _trace_sample(self, baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
        rows: list[str] = []
        for arm, report in (("baseline", baseline), ("candidate", candidate)):
            for task in report.get("tasks", []):
                if not isinstance(task, Mapping) or not task.get("trace_id"):
                    continue
                events = self.event_store.list_events(trace_id=str(task["trace_id"]))
                for event in events:
                    if event.type in _PUBLIC_EVENT_TYPES:
                        rows.append(canonical_json(_public_event(event, arm)))
        return "\n".join(rows) + ("\n" if rows else "")


def _public_manifest_arm(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eval_run_id": value.get("eval_run_id"),
        "source_reproducibility_digest": value.get("reproducibility_digest"),
        "experiment_variable": _redact(
            value.get("experiment_variable") or {}, redact_content=True
        ),
        "reproducibility": _redact(value.get("reproducibility") or {}, redact_content=True),
    }


def _public_run_metrics(report: Mapping[str, Any], diagnoses: Any) -> dict[str, Any]:
    tasks = []
    for task in report.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        tasks.append(
            {
                "task_id": task.get("task_id"),
                "status": task.get("status"),
                "evaluator": _redact(task.get("evaluator") or {}),
                "metrics": _redact(task.get("metrics") or {}),
                "prediction_digest": task.get("prediction_digest"),
                "error": _redact(task.get("error") or {}),
            }
        )
    return {
        "eval_run_id": report.get("eval_run_id"),
        "manifest_digest": report.get("manifest_digest"),
        "metrics": _redact(report.get("metrics") or {}),
        "tasks": tasks,
        "diagnoses": _redact(diagnoses if isinstance(diagnoses, list) else []),
    }


def _public_event(event: AgentEvent, arm: str) -> dict[str, Any]:
    value = event.to_dict()
    value["payload"] = _redact(value.get("payload") or {}, redact_content=True)
    if value.get("native_payload_ref"):
        value["native_payload_ref"] = "<redacted:native-payload-ref>"
    value["public_export"] = {"arm": arm, "redacted": True}
    return value


def _redact(value: Any, key: str = "", *, redact_content: bool = False) -> Any:
    lowered = key.lower()
    if lowered in _SECRET_KEYS or any(part in lowered for part in _SECRET_KEY_PARTS):
        return "<redacted:credential>"
    if lowered == "uri" and isinstance(value, str) and value.startswith((".praxile/", "file:")):
        return "<redacted:artifact-uri>"
    if redact_content and lowered in _CONTENT_KEYS:
        return f"<redacted:{lowered.replace('_', '-')}>"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact(item, str(item_key), redact_content=redact_content)
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, key, redact_content=redact_content) for item in value]
    if isinstance(value, str):
        return _redact_paths(value)
    return value


def _redact_paths(value: str) -> str:
    if value.startswith(("/", "file:///")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return "<redacted:absolute-path>"
    value = re.sub(
        r"(?<![:/A-Za-z0-9])/(?:[^\s/'\"<>]+/)+[^\s'\"<>]*",
        "<redacted:absolute-path>",
        value,
    )
    return re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s\\/'\"<>]+[\\/])+[^\s'\"<>]*",
        "<redacted:absolute-path>",
        value,
    )


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FileNotFoundError(f"missing or invalid {name}")
    return value


def _safe_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", value):
        raise EvalSchemaError(f"unsafe experiment_id: {value!r}")
