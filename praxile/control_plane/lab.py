from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..adapters import AdapterPolicy, AgentAdapterV2, validate_adapter_v2
from ..eval.v2.runner import BenchmarkEvalRunner
from ..eval.v2.schema import EvalSchemaError, EvalTaskSet, canonical_json
from ..eval.v2.evaluator import TaskEvaluator
from ..trace import EventStore
from ..utils import read_json, utc_now, write_json
from .evolution import HarnessCandidate


HARNESS_LAB_MANIFEST_SCHEMA = "praxile.executable_harness_manifest.v1"
HARNESS_LAB_REPORT_SCHEMA = "praxile.executable_harness_report.v1"
COVERAGE_CLASSES = ("E", "T", "C", "S", "L", "V")
EXPERIMENT_MODES = frozenset({"creation", "evolution"})
ISOLATION_MODES = frozenset({"worktree", "container"})
COVERAGE_EVENTS = {
    "E": frozenset({"RUN_START", "CHECKPOINT", "FINAL_RESULT", "RUN_END"}),
    "T": frozenset({"MODEL_CALL", "TOOL_CALL", "TOOL_RESULT", "ARTIFACT_CHANGE"}),
    "C": frozenset({"CONTEXT_ACTIVATION", "CONTEXT_REPRESENTATION", "CONTEXT_SOURCE_USAGE", "CONTEXT_INJECT"}),
    "S": frozenset({"SKILL_REFERENCE", "SUBAGENT_START", "SUBAGENT_END"}),
    "L": frozenset(),
    "V": frozenset({"VERIFICATION"}),
}


@dataclass(frozen=True)
class HarnessMechanism:
    mechanism_id: str
    coverage_class: str
    expected_event_types: tuple[str, ...] = ()
    required: bool = True

    def __post_init__(self) -> None:
        _safe(self.mechanism_id, "mechanism_id")
        if self.coverage_class not in COVERAGE_CLASSES:
            raise EvalSchemaError(f"unsupported coverage class: {self.coverage_class!r}")
        if any(not str(item).strip() for item in self.expected_event_types):
            raise EvalSchemaError("expected_event_types must contain non-empty names")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HarnessMechanism":
        raw_events = value.get("expected_event_types", [])
        if not isinstance(raw_events, list):
            raise EvalSchemaError("mechanism expected_event_types must be an array")
        return cls(
            mechanism_id=str(value.get("mechanism_id") or ""),
            coverage_class=str(value.get("coverage_class") or ""),
            expected_event_types=tuple(str(item) for item in raw_events),
            required=bool(value.get("required", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mechanism_id": self.mechanism_id,
            "coverage_class": self.coverage_class,
            "expected_event_types": list(self.expected_event_types),
            "required": self.required,
        }


@dataclass(frozen=True)
class ExecutableHarnessManifest:
    lab_id: str
    version: str
    experiment_mode: str
    isolation_mode: str
    candidate: HarnessCandidate
    adapter_name: str
    executor_config: Mapping[str, Any]
    model: Mapping[str, Any]
    evaluator: Mapping[str, Any]
    baseline_policy: AdapterPolicy
    candidate_policy: AdapterPolicy
    development_task_ids: tuple[str, ...]
    heldout_task_ids: tuple[str, ...]
    development_task_digest: str
    heldout_task_digest: str
    repetitions: int
    mechanisms: tuple[HarnessMechanism, ...]
    base_harness_ref: str | None = None
    schema_version: str = HARNESS_LAB_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != HARNESS_LAB_MANIFEST_SCHEMA:
            raise EvalSchemaError(f"unsupported harness lab manifest: {self.schema_version}")
        _safe(self.lab_id, "lab_id")
        if not self.version.strip():
            raise EvalSchemaError("manifest version is required")
        if self.experiment_mode not in EXPERIMENT_MODES:
            raise EvalSchemaError(f"unsupported experiment mode: {self.experiment_mode!r}")
        if self.isolation_mode not in ISOLATION_MODES:
            raise EvalSchemaError(f"unsupported isolation mode: {self.isolation_mode!r}")
        for name, value in (("executor_config", self.executor_config), ("model", self.model), ("evaluator", self.evaluator)):
            if not isinstance(value, Mapping):
                raise EvalSchemaError(f"manifest {name} must be an object")
            canonical_json(dict(value))
        if self.candidate.executor_profile == "default" or self.candidate.task_family == "default":
            raise EvalSchemaError("executable candidates require explicit executor_profile and task_family")
        if self.experiment_mode == "evolution" and not self.base_harness_ref:
            raise EvalSchemaError("evolution experiments require base_harness_ref")
        if self.experiment_mode == "creation" and self.base_harness_ref:
            raise EvalSchemaError("creation experiments cannot declare base_harness_ref")
        if canonical_json(self.baseline_policy.to_dict()) == canonical_json(self.candidate_policy.to_dict()):
            raise EvalSchemaError("baseline and candidate harness policies must differ")
        if self.repetitions < 2:
            raise EvalSchemaError("harness lab requires at least two repetitions")
        if not self.development_task_ids or not self.heldout_task_ids:
            raise EvalSchemaError("development and held-out task IDs are both required")
        if set(self.development_task_ids) & set(self.heldout_task_ids):
            raise EvalSchemaError("development and held-out task IDs overlap")
        for name, digest in (("development_task_digest", self.development_task_digest), ("heldout_task_digest", self.heldout_task_digest)):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise EvalSchemaError(f"{name} must be a sha256 digest")
        if not self.mechanisms:
            raise EvalSchemaError("manifest must declare executable mechanisms")
        if len({item.mechanism_id for item in self.mechanisms}) != len(self.mechanisms):
            raise EvalSchemaError("manifest mechanism IDs must be unique")
        if self.adapter_name != self.candidate.executor_profile.split(".", 1)[0]:
            raise EvalSchemaError("executor_profile must begin with the fixed adapter name")
        if not str(self.model.get("model_name_or_path") or self.model.get("model") or "").strip():
            raise EvalSchemaError("manifest model identity is required")

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExecutableHarnessManifest":
        for name in ("executor_config", "model", "evaluator", "baseline_policy", "candidate_policy", "candidate"):
            if not isinstance(value.get(name), Mapping):
                raise EvalSchemaError(f"manifest {name} must be an object")
        raw_mechanisms = value.get("mechanisms")
        if not isinstance(raw_mechanisms, list):
            raise EvalSchemaError("manifest mechanisms must be an array")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            lab_id=str(value.get("lab_id") or ""),
            version=str(value.get("version") or ""),
            experiment_mode=str(value.get("experiment_mode") or ""),
            isolation_mode=str(value.get("isolation_mode") or ""),
            candidate=HarnessCandidate.from_dict(value["candidate"]),
            adapter_name=str(value.get("adapter_name") or ""),
            executor_config=dict(value["executor_config"]),
            model=dict(value["model"]),
            evaluator=dict(value["evaluator"]),
            baseline_policy=AdapterPolicy.from_dict(value["baseline_policy"]),
            candidate_policy=AdapterPolicy.from_dict(value["candidate_policy"]),
            development_task_ids=_strings(value.get("development_task_ids"), "development_task_ids"),
            heldout_task_ids=_strings(value.get("heldout_task_ids"), "heldout_task_ids"),
            development_task_digest=str(value.get("development_task_digest") or ""),
            heldout_task_digest=str(value.get("heldout_task_digest") or ""),
            repetitions=int(value.get("repetitions", 0)),
            mechanisms=tuple(HarnessMechanism.from_dict(item) for item in raw_mechanisms),
            base_harness_ref=str(value.get("base_harness_ref") or "") or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "lab_id": self.lab_id,
            "version": self.version,
            "experiment_mode": self.experiment_mode,
            "isolation_mode": self.isolation_mode,
            "candidate": self.candidate.to_dict(),
            "promotion_key": self.candidate.promotion_key,
            "adapter_name": self.adapter_name,
            "executor_config": dict(self.executor_config),
            "model": dict(self.model),
            "evaluator": dict(self.evaluator),
            "baseline_policy": self.baseline_policy.to_dict(),
            "candidate_policy": self.candidate_policy.to_dict(),
            "development_task_ids": list(self.development_task_ids),
            "heldout_task_ids": list(self.heldout_task_ids),
            "development_task_digest": self.development_task_digest,
            "heldout_task_digest": self.heldout_task_digest,
            "repetitions": self.repetitions,
            "mechanisms": [item.to_dict() for item in self.mechanisms],
            "base_harness_ref": self.base_harness_ref,
        }


class ExecutableHarnessLab:
    """Execute frozen Harness versions through the existing isolated benchmark runner."""

    def __init__(self, state_root: Path, event_store: EventStore):
        self.state_root = state_root.resolve()
        self.event_store = event_store
        self.runner = BenchmarkEvalRunner(self.state_root, event_store)

    def run(
        self,
        manifest: ExecutableHarnessManifest,
        development: EvalTaskSet,
        heldout: EvalTaskSet,
        *,
        adapter: AgentAdapterV2,
        evaluator: TaskEvaluator,
        resume: bool = False,
        keep_workspaces: bool = False,
        source_overrides: Mapping[str, Path] | None = None,
    ) -> dict[str, Any]:
        self._validate_runtime(manifest, development, heldout, adapter, evaluator)
        root = self._root(manifest.lab_id)
        self._write_manifest(root, manifest)
        arms: list[dict[str, Any]] = []
        for split, task_set in (("development", development), ("heldout", heldout)):
            for repetition in range(1, manifest.repetitions + 1):
                reports: dict[str, Any] = {}
                for arm, policy in (("baseline", manifest.baseline_policy), ("candidate", manifest.candidate_policy)):
                    run_id = f"{manifest.lab_id}.{split}.r{repetition}.{arm}"
                    reports[arm] = self.runner.run(
                        task_set,
                        adapter=adapter,
                        evaluator=evaluator,
                        policy=policy,
                        model=manifest.model,
                        eval_run_id=run_id,
                        resume=resume,
                        keep_workspaces=keep_workspaces,
                        source_overrides=source_overrides,
                        experiment_variable={
                            "lab_id": manifest.lab_id,
                            "manifest_digest": manifest.digest,
                            "experiment_mode": manifest.experiment_mode,
                            "split": split,
                            "repetition": repetition,
                            "arm": arm,
                            "promotion_key": manifest.candidate.promotion_key,
                        },
                    )
                arms.append(self._arm_result(split, repetition, reports["baseline"], reports["candidate"]))
        report = self._report(manifest, arms)
        write_json(root / "report.json", report)
        return report

    def _validate_runtime(
        self,
        manifest: ExecutableHarnessManifest,
        development: EvalTaskSet,
        heldout: EvalTaskSet,
        adapter: AgentAdapterV2,
        evaluator: TaskEvaluator,
    ) -> None:
        capabilities = validate_adapter_v2(adapter)
        if adapter.name != manifest.adapter_name:
            raise EvalSchemaError("runtime adapter differs from frozen executor profile")
        if canonical_json(evaluator.identity()) != canonical_json(manifest.evaluator):
            raise EvalSchemaError("runtime evaluator differs from frozen harness manifest")
        if tuple(task.task_id for task in development.tasks) != manifest.development_task_ids:
            raise EvalSchemaError("development task set differs from frozen harness manifest")
        if tuple(task.task_id for task in heldout.tasks) != manifest.heldout_task_ids:
            raise EvalSchemaError("held-out task set differs from frozen harness manifest")
        if development.digest != manifest.development_task_digest:
            raise EvalSchemaError("development task-set digest differs from frozen harness manifest")
        if heldout.digest != manifest.heldout_task_digest:
            raise EvalSchemaError("held-out task-set digest differs from frozen harness manifest")
        if manifest.isolation_mode == "container" and capabilities.metadata.get("isolation_mode") != "container":
            raise EvalSchemaError("container lab requires an adapter that declares container isolation")

    def _arm_result(
        self,
        split: str,
        repetition: int,
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        left = _outcomes(baseline)
        right = _outcomes(candidate)
        if set(left) != set(right):
            raise EvalSchemaError("baseline and candidate task IDs differ")
        rows = [
            {
                "task_id": task_id,
                "baseline_resolved": left[task_id],
                "candidate_resolved": right[task_id],
                "transition": _transition(left[task_id], right[task_id]),
            }
            for task_id in sorted(left)
        ]
        coverage = self._coverage(candidate)
        return {
            "split": split,
            "repetition": repetition,
            "baseline_run_id": baseline.get("eval_run_id"),
            "candidate_run_id": candidate.get("eval_run_id"),
            "tasks": rows,
            "coverage": coverage,
            "efficiency": {
                "baseline_cost": float((baseline.get("metrics") or {}).get("cost", 0.0) or 0.0),
                "candidate_cost": float((candidate.get("metrics") or {}).get("cost", 0.0) or 0.0),
                "baseline_tokens": _token_total(baseline),
                "candidate_tokens": _token_total(candidate),
            },
        }

    def _coverage(self, report: Mapping[str, Any]) -> dict[str, Any]:
        event_counts = {event_type: 0 for values in COVERAGE_EVENTS.values() for event_type in values}
        verification_count = 0
        task_count = 0
        for task in report.get("tasks", []):
            if not isinstance(task, Mapping):
                continue
            task_count += 1
            trace_id = str(task.get("trace_id") or "")
            for event in self.event_store.list_events(trace_id=trace_id) if trace_id else []:
                event_counts[event.type] = event_counts.get(event.type, 0) + 1
            if (task.get("evaluator") or {}).get("resolved") is not None:
                verification_count += 1
        counts = {
            coverage: (
                task_count if coverage == "L"
                else verification_count if coverage == "V"
                else sum(event_counts.get(name, 0) for name in event_types)
            )
            for coverage, event_types in COVERAGE_EVENTS.items()
        }
        return {"counts": counts, "covered": {key: value > 0 for key, value in counts.items()}, "event_counts": event_counts}

    def _report(self, manifest: ExecutableHarnessManifest, arms: list[dict[str, Any]]) -> dict[str, Any]:
        split_stats = {split: _statistics([item for item in arms if item["split"] == split]) for split in ("development", "heldout")}
        aggregate_counts = {key: 0 for key in COVERAGE_CLASSES}
        aggregate_events: dict[str, int] = {}
        for arm in arms:
            for key, count in arm["coverage"]["counts"].items():
                aggregate_counts[key] += int(count)
            for key, count in arm["coverage"]["event_counts"].items():
                aggregate_events[key] = aggregate_events.get(key, 0) + int(count)
        mechanisms = []
        for mechanism in manifest.mechanisms:
            if mechanism.expected_event_types:
                activations = sum(aggregate_events.get(name, 0) for name in mechanism.expected_event_types)
            else:
                activations = aggregate_counts[mechanism.coverage_class]
            mechanisms.append({**mechanism.to_dict(), "activation_count": activations, "dead": activations == 0})
        required_dead = [item["mechanism_id"] for item in mechanisms if item["required"] and item["dead"]]
        coverage = {
            "definitions": {
                "E": "execution/environment lifecycle",
                "T": "model, tool, and artifact interaction",
                "C": "context activation, representation, usage, and injection",
                "S": "skill and subagent execution",
                "L": "versioned harness candidate applied to an executable arm",
                "V": "independent evaluator outcome available",
            },
            "counts": aggregate_counts,
            "covered": {key: value > 0 for key, value in aggregate_counts.items()},
        }
        return {
            "schema_version": HARNESS_LAB_REPORT_SCHEMA,
            "lab_id": manifest.lab_id,
            "manifest_digest": manifest.digest,
            "created_at": utc_now(),
            "experiment_mode": manifest.experiment_mode,
            "isolation_mode": manifest.isolation_mode,
            "promotion_key": manifest.candidate.promotion_key,
            "executor_profile": manifest.candidate.executor_profile,
            "task_family": manifest.candidate.task_family,
            "repetitions": manifest.repetitions,
            "invariant_check": {
                "valid": True,
                "fixed": ["adapter", "executor_profile", "model", "evaluator", "task_set", "isolation_mode"],
                "changed": ["harness_policy"],
            },
            "splits": split_stats,
            "runtime_coverage": coverage,
            "mechanisms": mechanisms,
            "dead_mechanisms": required_dead,
            "promotion_eligible": bool(
                not required_dead
                and split_stats["heldout"]["delta"]["estimate"] > 0
                and split_stats["heldout"]["delta"]["ci95"][0] >= 0
            ),
            "arms": arms,
        }

    def _write_manifest(self, root: Path, manifest: ExecutableHarnessManifest) -> None:
        path = root / "manifest.json"
        payload = {**manifest.to_dict(), "digest": manifest.digest}
        if path.exists():
            current = read_json(path, None)
            if canonical_json(current) != canonical_json(payload):
                raise EvalSchemaError("immutable harness lab manifest already exists with different content")
            return
        write_json(path, payload)

    def _root(self, lab_id: str) -> Path:
        _safe(lab_id, "lab_id")
        return self.state_root / "eval" / "v2" / "harness-lab" / lab_id


class ExecutorCompatibilityMatrix:
    @staticmethod
    def build(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
        rows = []
        seen: set[tuple[str, str]] = set()
        for report in reports:
            executor = str(report.get("executor_profile") or "")
            family = str(report.get("task_family") or "")
            key = (executor, family)
            if not executor or not family or key in seen:
                raise EvalSchemaError("compatibility reports require unique executor_profile/task_family rows")
            seen.add(key)
            heldout = (report.get("splits") or {}).get("heldout") or {}
            delta = heldout.get("delta") or {}
            rows.append({
                "executor_profile": executor,
                "task_family": family,
                "promotion_key": report.get("promotion_key"),
                "estimate": delta.get("estimate"),
                "ci95": delta.get("ci95"),
                "dead_mechanisms": list(report.get("dead_mechanisms") or []),
                "compatible": bool(report.get("promotion_eligible")),
            })
        directions = {"positive" if float(row["estimate"] or 0) > 0 else "negative" if float(row["estimate"] or 0) < 0 else "neutral" for row in rows}
        return {
            "schema_version": "praxile.executor_compatibility_matrix.v1",
            "rows": rows,
            "executor_sensitive": len(directions) > 1,
            "promotion_scope": "component_id + executor_profile + task_family",
        }


def _statistics(arms: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for arm in arms for row in arm["tasks"]]
    baseline = [row["baseline_resolved"] for row in rows if row["baseline_resolved"] is not None]
    candidate = [row["candidate_resolved"] for row in rows if row["candidate_resolved"] is not None]
    base_success = sum(value is True for value in baseline)
    candidate_success = sum(value is True for value in candidate)
    base_interval = _wilson(base_success, len(baseline))
    candidate_interval = _wilson(candidate_success, len(candidate))
    base_rate = base_success / len(baseline) if baseline else 0.0
    candidate_rate = candidate_success / len(candidate) if candidate else 0.0
    cost_deltas = [float(arm["efficiency"]["candidate_cost"]) - float(arm["efficiency"]["baseline_cost"]) for arm in arms]
    token_deltas = [int(arm["efficiency"]["candidate_tokens"]) - int(arm["efficiency"]["baseline_tokens"]) for arm in arms]
    return {
        "sample_count": len(rows),
        "repetition_count": len(arms),
        "baseline": {"resolved": base_success, "n": len(baseline), "rate": round(base_rate, 4), "ci95": base_interval},
        "candidate": {"resolved": candidate_success, "n": len(candidate), "rate": round(candidate_rate, 4), "ci95": candidate_interval},
        "delta": {
            "estimate": round(candidate_rate - base_rate, 4),
            "ci95": [round(candidate_interval[0] - base_interval[1], 4), round(candidate_interval[1] - base_interval[0], 4)],
        },
        "gains": sum(row["transition"] == "improved" for row in rows),
        "regressions": sum(row["transition"] == "regressed" for row in rows),
        "unknown": sum(row["transition"] == "unknown" for row in rows),
        "efficiency": {
            "cost_delta_mean": round(sum(cost_deltas) / len(cost_deltas), 8) if cost_deltas else None,
            "cost_delta_ci95": _mean_ci(cost_deltas),
            "token_delta_mean": round(sum(token_deltas) / len(token_deltas), 2) if token_deltas else None,
            "token_delta_ci95": _mean_ci(token_deltas),
        },
    }


def _wilson(successes: int, total: int) -> list[float]:
    if not total:
        return [0.0, 1.0]
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def _mean_ci(values: list[float | int]) -> list[float] | None:
    if not values:
        return None
    average = sum(float(item) for item in values) / len(values)
    if len(values) == 1:
        return [round(average, 4), round(average, 4)]
    variance = sum((float(item) - average) ** 2 for item in values) / (len(values) - 1)
    margin = 1.959963984540054 * math.sqrt(variance / len(values))
    return [round(average - margin, 4), round(average + margin, 4)]


def _token_total(report: Mapping[str, Any]) -> int:
    tokens = (report.get("metrics") or {}).get("tokens") or {}
    return sum(int(tokens.get(key, 0) or 0) for key in ("input", "output", "cache"))


def _outcomes(report: Mapping[str, Any]) -> dict[str, bool | None]:
    return {
        str(item["task_id"]): (item.get("evaluator") or {}).get("resolved")
        for item in report.get("tasks", [])
        if isinstance(item, Mapping)
    }


def _transition(left: bool | None, right: bool | None) -> str:
    if left is None or right is None:
        return "unknown"
    if not left and right:
        return "improved"
    if left and not right:
        return "regressed"
    return "unchanged"


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
        raise EvalSchemaError(f"{name} must be a non-empty string array")
    result = tuple(str(item) for item in value)
    if len(result) != len(set(result)):
        raise EvalSchemaError(f"{name} contains duplicates")
    return result


def _safe(value: str, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", value):
        raise EvalSchemaError(f"unsafe {name}: {value!r}")
