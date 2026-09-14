from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...adapters import AdapterPolicy, AgentAdapterV2
from ...control_plane import ContextPolicy
from ...trace import EventStore
from ...utils import read_json, utc_now, write_json
from .diagnosis import FailureDiagnoser
from .evaluator import TaskEvaluator
from .experiment import compare_ab_reports
from .manifest import ImmutableManifestStore
from .runner import BenchmarkEvalRunner
from .schema import EvalSchemaError, EvalTaskSet, canonical_json


CONTEXT_ABLATION_SCHEMA_VERSION = "praxile.context_ablation.v1"


class ContextPolicyAblation:
    """Compare two complete Context Policies while freezing every execution invariant."""

    def __init__(self, state_root: Path, event_store: EventStore):
        self.state_root = state_root.resolve()
        self.event_store = event_store
        self.benchmark = BenchmarkEvalRunner(self.state_root, event_store)
        self.manifests = ImmutableManifestStore(self.state_root)
        self.diagnoser = FailureDiagnoser()

    def run(
        self,
        task_set: EvalTaskSet,
        *,
        adapter: AgentAdapterV2,
        evaluator: TaskEvaluator,
        policy_a: ContextPolicy,
        policy_b: ContextPolicy,
        context_a: tuple[Mapping[str, Any], ...],
        context_b: tuple[Mapping[str, Any], ...],
        model: Mapping[str, Any],
        experiment_id: str,
        execution_policy: AdapterPolicy | None = None,
        resume: bool = False,
        keep_workspaces: bool = False,
        source_overrides: Mapping[str, Path] | None = None,
    ) -> dict[str, Any]:
        adapter_a = _overlay_execution(
            policy_a.compile(context_a, evaluation=policy_a.status == "candidate"), execution_policy
        )
        adapter_b = _overlay_execution(
            policy_b.compile(context_b, evaluation=policy_b.status == "candidate"), execution_policy
        )
        root = self._root(experiment_id)
        plan_path = root / "manifest.json"
        if plan_path.exists() and not resume:
            raise EvalSchemaError(f"context ablation already exists; resume it explicitly: {experiment_id}")
        plan = {
            "schema_version": CONTEXT_ABLATION_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": utc_now(),
            "track": "clean",
            "task_set_digest": task_set.digest,
            "arms": {
                "policy_a": {"run_id": f"{experiment_id}.policy_a", "policy": policy_a.to_dict()},
                "policy_b": {"run_id": f"{experiment_id}.policy_b", "policy": policy_b.to_dict()},
            },
            "changed_variable": "context_policy",
            "frozen_invariants": ["task_set", "adapter", "model", "evaluator", "execution"],
        }
        if plan_path.exists():
            existing = read_json(plan_path, None)
            if not isinstance(existing, Mapping):
                raise EvalSchemaError("context ablation manifest is invalid")
            plan["created_at"] = existing.get("created_at")
            if canonical_json(plan) != canonical_json(existing):
                raise EvalSchemaError("cannot resume context ablation with changed inputs")
        else:
            write_json(plan_path, plan)
        for arm, policy in (("policy_a", adapter_a), ("policy_b", adapter_b)):
            self.benchmark.run(
                task_set,
                adapter=adapter,
                evaluator=evaluator,
                policy=policy,
                model=model,
                eval_run_id=f"{experiment_id}.{arm}",
                resume=resume,
                keep_workspaces=keep_workspaces,
                source_overrides=source_overrides,
                experiment_variable={
                    "type": "context_policy",
                    "arm": arm,
                    "policy_id": policy.policy_id,
                    "policy_version": policy.version,
                    "changed_variable": "context_policy",
                },
            )
        return self.analyze(experiment_id)

    def analyze(self, experiment_id: str) -> dict[str, Any]:
        root = self._root(experiment_id)
        plan = read_json(root / "manifest.json", None)
        if not isinstance(plan, Mapping):
            raise FileNotFoundError(root / "manifest.json")
        arms = plan.get("arms")
        if not isinstance(arms, Mapping):
            raise EvalSchemaError("context ablation manifest has no arms")
        run_a = str(arms["policy_a"]["run_id"])
        run_b = str(arms["policy_b"]["run_id"])
        report_a = read_json(self.manifests.path_for(run_a).parent / "report.json", None)
        report_b = read_json(self.manifests.path_for(run_b).parent / "report.json", None)
        if not isinstance(report_a, Mapping) or not isinstance(report_b, Mapping):
            raise EvalSchemaError("both context ablation arm reports must exist")
        manifest_a = self.manifests.load(run_a)
        manifest_b = self.manifests.load(run_b)
        invariants = _check_invariants(manifest_a.reproducibility, manifest_b.reproducibility)
        if not invariants["valid"]:
            raise EvalSchemaError(f"context ablation invariants changed: {invariants['violations']}")
        diagnoses = {
            "baseline": self._diagnose(report_a),
            "candidate": self._diagnose(report_b),
        }
        comparison = compare_ab_reports(
            report_a,
            report_b,
            diagnoses=diagnoses,
            event_store=self.event_store,
            cost_comparable=_cost_comparable(manifest_a.reproducibility, manifest_b.reproducibility),
        )
        report = {
            "schema_version": CONTEXT_ABLATION_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": utc_now(),
            "changed_variable": "context_policy",
            "invariant_check": invariants,
            "baseline": _arm(report_a, arms["policy_a"]),
            "candidate": _arm(report_b, arms["policy_b"]),
            "diagnoses": diagnoses,
            "comparison": comparison,
        }
        write_json(root / "report.json", report)
        return report

    def _diagnose(self, report: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for result in report.get("tasks", []):
            if isinstance(result, Mapping) and result.get("trace_id"):
                trace_id = str(result["trace_id"])
                rows.append(
                    self.diagnoser.diagnose(
                        result,
                        self.event_store.list_events(trace_id=trace_id),
                        self.event_store.list_artifacts(trace_id),
                    ).to_dict()
                )
        return rows

    def _root(self, experiment_id: str) -> Path:
        if not experiment_id or not all(character.isalnum() or character in "_.-" for character in experiment_id):
            raise EvalSchemaError(f"unsafe experiment_id: {experiment_id!r}")
        return self.state_root / "eval" / "v2" / "context-ablations" / experiment_id


def _check_invariants(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_copy = dict(left)
    right_copy = dict(right)
    left_copy.pop("policy", None)
    right_copy.pop("policy", None)
    violations = [key for key in sorted(set(left_copy) | set(right_copy)) if canonical_json(left_copy.get(key)) != canonical_json(right_copy.get(key))]
    return {"valid": not violations, "violations": violations, "changed_variable": "context_policy"}


def _cost_comparable(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(item.get("model", {}).get("cost_tracking") != "ignore_errors" for item in (left, right))


def _arm(report: Mapping[str, Any], arm: Mapping[str, Any]) -> dict[str, Any]:
    policy = arm.get("policy", {})
    return {
        "eval_run_id": report.get("eval_run_id"),
        "manifest_digest": report.get("manifest_digest"),
        "metrics": dict(report.get("metrics") or {}),
        "policy_id": policy.get("policy_id"),
        "policy_version": policy.get("version"),
    }


def _overlay_execution(policy: AdapterPolicy, execution: AdapterPolicy | None) -> AdapterPolicy:
    if execution is None:
        return policy
    if execution.context:
        raise EvalSchemaError("context ablation execution policy must not inject context")
    return AdapterPolicy(
        policy_id=policy.policy_id,
        version=policy.version,
        context=policy.context,
        budgets={**dict(execution.budgets), **dict(policy.budgets)},
        settings={**dict(execution.settings), **dict(policy.settings)},
    )
