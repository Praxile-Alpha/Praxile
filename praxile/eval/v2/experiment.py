from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from ...adapters import AdapterPolicy, AgentAdapterV2
from ...trace import EventStore
from ...utils import file_lock, read_json, utc_now, write_json
from .activation import ContextActivationGate
from .candidate import ContextCandidate
from .capability import CapabilityProtocol
from .heldout import HeldoutUseLedger
from .representation import ExperienceRepresentationRouter
from .proxy import ProxyEvalRegistry
from .diagnosis import FailureDiagnoser
from .evaluator import TaskEvaluator
from .manifest import EvalRunManifest, ImmutableManifestStore
from .runner import BenchmarkEvalRunner
from .schema import EvalSchemaError, EvalTaskSet, canonical_json


AB_EXPERIMENT_SCHEMA_VERSION = "praxile.ab_experiment.v1"
AB_REPORT_SCHEMA_VERSION = "praxile.ab_report.v1"


class ControlledABExperiment:
    """Execute one clean-track context variable under checked P0 invariants."""

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
        baseline_policy: AdapterPolicy,
        candidate: ContextCandidate,
        model: Mapping[str, Any],
        experiment_id: str,
        resume: bool = False,
        keep_workspaces: bool = False,
        source_overrides: Mapping[str, Path] | None = None,
        capability_protocol: CapabilityProtocol | None = None,
        proxy_eval_ref: tuple[str, str] | None = None,
        development_only: bool = False,
    ) -> dict[str, Any]:
        if baseline_policy.context:
            raise EvalSchemaError("controlled P0 baseline policy must have empty context")
        if development_only and capability_protocol is not None:
            raise EvalSchemaError("development-only experiments cannot use a held-out capability protocol")
        if capability_protocol is not None:
            capability_protocol.validate_run(
                task_set, candidate_payload=candidate.to_dict(), evaluator_identity=evaluator.identity()
            )
        if proxy_eval_ref is not None and capability_protocol is None:
            raise EvalSchemaError("proxy eval requires a capability protocol")
        proxy_eval = None
        if proxy_eval_ref is not None:
            proxy_eval, _ = ProxyEvalRegistry(self.state_root).load_approved(*proxy_eval_ref)
            if proxy_eval.hypothesis_id != capability_protocol.operationalization.hypothesis_id:
                raise EvalSchemaError("proxy eval hypothesis differs from the capability protocol")
            if not set(proxy_eval.task_ids) <= set(capability_protocol.evaluation.development_task_ids):
                raise EvalSchemaError("proxy eval may only reference development tasks")
        candidate.validate_clean_track({task.task_id for task in task_set.tasks})
        activation_plan = ContextActivationGate().plan(candidate, task_set.tasks)
        representation_plan = (
            ExperienceRepresentationRouter().plan(candidate, task_set.tasks, activation_plan)
            if candidate.representation_options else None
        )
        candidate_policy = candidate.policy(
            baseline_policy, activation_plan=activation_plan, representation_plan=representation_plan
        )
        baseline_run_id = f"{experiment_id}.baseline"
        candidate_run_id = f"{experiment_id}.candidate"
        manifest_path = self._root(experiment_id) / "manifest.json"
        existing = read_json(manifest_path, None)
        if existing is not None and not resume:
            raise EvalSchemaError(f"A/B experiment already exists; resume it explicitly: {experiment_id}")
        plan = {
            "schema_version": AB_EXPERIMENT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": utc_now(),
            "track": "development" if development_only else "clean",
            "task_set_digest": task_set.digest,
            "baseline_run_id": baseline_run_id,
            "candidate_run_id": candidate_run_id,
            "candidate": candidate.to_dict(),
            "candidate_digest": candidate.digest,
            "activation_gate": activation_plan,
            **({"representation_plan": representation_plan} if representation_plan is not None else {}),
            "changed_variable": "policy.context[0]",
            "frozen_invariants": [
                "task_set",
                "adapter",
                "model",
                "evaluator",
                "execution",
                "policy.budgets",
                "policy.settings",
            ],
        }
        if capability_protocol is not None:
            plan["capability_protocol"] = capability_protocol.to_dict()
            plan["capability_protocol_digest"] = capability_protocol.digest
            plan["information_boundary"] = {
                "execution_projection": "task_instruction_and_public_metadata_only",
                "evaluator_owned_by": capability_protocol.evaluation.evaluator_owner,
                "os_sandbox": False,
                "confidentiality_level": capability_protocol.information_boundary.isolation_level,
            }
        if proxy_eval is not None:
            plan["proxy_eval"] = {"proxy_id": proxy_eval.proxy_id, "version": proxy_eval.version,
                                  "digest": proxy_eval.digest}
        if isinstance(existing, Mapping):
            plan["created_at"] = existing.get("created_at")
            if canonical_json(plan) != canonical_json(existing):
                raise EvalSchemaError("cannot resume A/B with a changed capability, proxy, or experiment manifest")
        if capability_protocol is not None:
            heldout = HeldoutUseLedger(self.state_root)
            heldout.reserve(
                capability_protocol, experiment_id=experiment_id,
                candidate_digest=candidate.digest, task_set_digest=task_set.digest,
            )
            if heldout.status(capability_protocol) == "evaluated":
                if not resume:
                    raise EvalSchemaError("held-out evaluation was already finalized")
                return self.analyze(experiment_id)
        _write_once(manifest_path, plan)
        baseline_report = self.benchmark.run(
            task_set,
            adapter=adapter,
            evaluator=evaluator,
            policy=baseline_policy,
            model=model,
            eval_run_id=baseline_run_id,
            resume=resume,
            keep_workspaces=keep_workspaces,
            source_overrides=source_overrides,
        )
        candidate_report = self.benchmark.run(
            task_set,
            adapter=adapter,
            evaluator=evaluator,
            policy=candidate_policy,
            model=model,
            eval_run_id=candidate_run_id,
            resume=resume,
            keep_workspaces=keep_workspaces,
            source_overrides=source_overrides,
            experiment_variable={
                "type": candidate.candidate_type,
                "candidate_id": candidate.candidate_id,
                "candidate_version": candidate.version,
                "candidate_digest": candidate.digest,
                "changed_variable": "policy.context[0]",
            },
        )
        return self._analyze(experiment_id)

    def analyze(self, experiment_id: str) -> dict[str, Any]:
        if HeldoutUseLedger(self.state_root).experiment_status(experiment_id) == "reserved":
            raise EvalSchemaError("held-out feedback is sealed until the experiment is finalized")
        return self._analyze(experiment_id)

    def _analyze(self, experiment_id: str) -> dict[str, Any]:
        root = self._root(experiment_id)
        plan = read_json(root / "manifest.json", None)
        if not isinstance(plan, Mapping):
            raise FileNotFoundError(root / "manifest.json")
        candidate_value = plan.get("candidate")
        if not isinstance(candidate_value, Mapping):
            raise EvalSchemaError("A/B manifest does not contain a context candidate")
        candidate = ContextCandidate.from_dict(candidate_value)
        baseline_run_id = str(plan.get("baseline_run_id") or "")
        candidate_run_id = str(plan.get("candidate_run_id") or "")
        baseline_report = read_json(self.manifests.path_for(baseline_run_id).parent / "report.json", None)
        candidate_report = read_json(self.manifests.path_for(candidate_run_id).parent / "report.json", None)
        if not isinstance(baseline_report, Mapping) or not isinstance(candidate_report, Mapping):
            raise EvalSchemaError("both A/B arm reports must exist before analysis")
        baseline_manifest = self.manifests.load(baseline_run_id)
        candidate_manifest = self.manifests.load(candidate_run_id)
        activation_plan = plan.get("activation_gate")
        representation_plan = plan.get("representation_plan")
        invariant_check = check_ab_invariants(
            baseline_manifest,
            candidate_manifest,
            candidate,
            activation_plan=activation_plan if isinstance(activation_plan, Mapping) else None,
            representation_plan=representation_plan if isinstance(representation_plan, Mapping) else None,
        )
        if not invariant_check["valid"]:
            raise EvalSchemaError(f"A/B invariants changed: {invariant_check['violations']}")
        diagnoses = {
            "baseline": self._diagnose_arm(experiment_id, "baseline", baseline_report),
            "candidate": self._diagnose_arm(experiment_id, "candidate", candidate_report),
        }
        comparison = compare_ab_reports(
            baseline_report,
            candidate_report,
            diagnoses=diagnoses,
            event_store=self.event_store,
            cost_comparable=_cost_comparable(baseline_manifest, candidate_manifest),
        )
        capability_value = plan.get("capability_protocol")
        capability_protocol = None
        if capability_value is not None:
            capability_protocol = CapabilityProtocol.from_dict(capability_value)
            if capability_protocol.digest != plan.get("capability_protocol_digest"):
                raise EvalSchemaError("A/B capability protocol digest mismatch")
        report = {
            "schema_version": AB_REPORT_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "created_at": utc_now(),
            "track": plan.get("track", "clean"),
            "candidate_id": candidate.candidate_id,
            "candidate_digest": candidate.digest,
            "activation_gate": dict(activation_plan) if isinstance(activation_plan, Mapping) else None,
            "representation_plan": dict(representation_plan) if isinstance(representation_plan, Mapping) else None,
            "invariant_check": invariant_check,
            "baseline": _arm_summary(baseline_report),
            "candidate": _arm_summary(candidate_report),
            "diagnoses": diagnoses,
            "comparison": comparison,
        }
        if capability_protocol is not None:
            proxy_result = None
            proxy_ref = plan.get("proxy_eval")
            if isinstance(proxy_ref, Mapping):
                proxy_eval, approval = ProxyEvalRegistry(self.state_root).load_approved(
                    str(proxy_ref.get("proxy_id")), str(proxy_ref.get("version"))
                )
                if proxy_eval.digest != proxy_ref.get("digest"):
                    raise EvalSchemaError("A/B proxy eval digest mismatch")
                if proxy_eval.hypothesis_id != capability_protocol.operationalization.hypothesis_id:
                    raise EvalSchemaError("A/B proxy eval hypothesis mismatch")
                if not set(proxy_eval.task_ids) <= set(capability_protocol.evaluation.development_task_ids):
                    raise EvalSchemaError("A/B proxy eval references held-out tasks")
                proxy_result = proxy_eval.evaluate(comparison["task_results"])
                proxy_result["approved_by"] = approval["reviewer"]
            report["capability"] = {
                "goal_id": capability_protocol.goal.goal_id,
                "goal_version": capability_protocol.goal.version,
                "hypothesis_id": capability_protocol.operationalization.hypothesis_id,
                "contract_id": capability_protocol.evaluation.contract_id,
                "protocol_digest": capability_protocol.digest,
                "information_boundary": dict(plan["information_boundary"]),
                "terminal_selection": capability_protocol.select_terminal(comparison["task_results"]),
                "proxy_eval": proxy_result,
            }
        write_json(self._root(experiment_id) / "report.json", report)
        if capability_protocol is not None and HeldoutUseLedger(self.state_root).status(capability_protocol):
            report_digest = "sha256:" + hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
            HeldoutUseLedger(self.state_root).mark_evaluated(
                capability_protocol, experiment_id=experiment_id, report_digest=report_digest
            )
        return report

    def _diagnose_arm(
        self, experiment_id: str, arm: str, report: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        diagnoses: list[dict[str, Any]] = []
        for result in report.get("tasks", []):
            if not isinstance(result, Mapping) or not result.get("trace_id"):
                continue
            trace_id = str(result["trace_id"])
            diagnosis = self.diagnoser.diagnose(
                result,
                self.event_store.list_events(trace_id=trace_id),
                self.event_store.list_artifacts(trace_id),
            )
            payload = diagnosis.to_dict()
            write_json(self._root(experiment_id) / "diagnoses" / arm / f"{diagnosis.task_id}.json", payload)
            diagnoses.append(payload)
        return diagnoses

    def _root(self, experiment_id: str) -> Path:
        if not experiment_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in experiment_id):
            raise EvalSchemaError(f"unsafe experiment_id: {experiment_id!r}")
        return self.state_root / "eval" / "v2" / "experiments" / experiment_id


def check_ab_invariants(
    baseline: EvalRunManifest,
    candidate: EvalRunManifest,
    context_candidate: ContextCandidate,
    *,
    activation_plan: Mapping[str, Any] | None = None,
    representation_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    left = dict(baseline.reproducibility)
    right = dict(candidate.reproducibility)
    left_policy = dict(left.pop("policy", {}))
    right_policy = dict(right.pop("policy", {}))
    violations: list[str] = []
    if canonical_json(left) != canonical_json(right):
        for key in sorted(set(left) | set(right)):
            if canonical_json(left.get(key)) != canonical_json(right.get(key)):
                violations.append(key)
    if left_policy.get("context") != []:
        violations.append("baseline.policy.context")
    expected = context_candidate.policy(
        AdapterPolicy(
            policy_id=str(left_policy.get("policy_id") or "baseline"),
            version=str(left_policy.get("version") or "1"),
            budgets=dict(left_policy.get("budgets") or {}),
            settings=dict(left_policy.get("settings") or {}),
        ),
        activation_plan=activation_plan,
        representation_plan=representation_plan,
    ).to_dict()
    if right_policy != expected:
        for key in sorted(set(right_policy) | set(expected)):
            if canonical_json(right_policy.get(key)) != canonical_json(expected.get(key)):
                violations.append(f"candidate.policy.{key}")
    expected_variable = {
        "type": context_candidate.candidate_type,
        "candidate_id": context_candidate.candidate_id,
        "candidate_version": context_candidate.version,
        "candidate_digest": context_candidate.digest,
        "changed_variable": "policy.context[0]",
    }
    if dict(baseline.experiment_variable):
        violations.append("baseline.experiment_variable")
    if dict(candidate.experiment_variable) != expected_variable:
        violations.append("candidate.experiment_variable")
    return {
        "valid": not violations,
        "violations": sorted(set(violations)),
        "changed_variable": "policy.context[0]",
        "baseline_manifest_digest": baseline.reproducibility_digest,
        "candidate_manifest_digest": candidate.reproducibility_digest,
    }


def compare_ab_reports(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    diagnoses: Mapping[str, list[dict[str, Any]]],
    event_store: EventStore,
    cost_comparable: bool,
) -> dict[str, Any]:
    baseline_tasks = {str(item["task_id"]): item for item in baseline.get("tasks", [])}
    candidate_tasks = {str(item["task_id"]): item for item in candidate.get("tasks", [])}
    if set(baseline_tasks) != set(candidate_tasks):
        raise EvalSchemaError("baseline and candidate task IDs differ")
    baseline_diagnoses = {item["task_id"]: item for item in diagnoses.get("baseline", [])}
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_rows: list[dict[str, Any]] = []
    for task_id in sorted(baseline_tasks):
        left = baseline_tasks[task_id]
        right = candidate_tasks[task_id]
        left_resolved = left.get("evaluator", {}).get("resolved")
        right_resolved = right.get("evaluator", {}).get("resolved")
        transition = _transition(left_resolved, right_resolved)
        category = str(
            baseline_diagnoses.get(task_id, {}).get("attribution", {}).get("category") or "UNKNOWN"
        )
        row = {
            "task_id": task_id,
            "failure_category": category,
            "baseline_resolved": left_resolved,
            "candidate_resolved": right_resolved,
            "transition": transition,
            "token_delta": _tokens(right) - _tokens(left),
            "tool_call_delta": _metric(right, "tool_calls") - _metric(left, "tool_calls"),
            "latency_ms_delta": _metric(right, "latency_ms") - _metric(left, "latency_ms"),
            "cost_delta": (
                round(_metric_float(right, "cost") - _metric_float(left, "cost"), 8)
                if cost_comparable
                else None
            ),
            "progress": _progress_comparison(left, right),
            "context_activation": right.get("context_activation"),
            "diff_scope": {
                "baseline": left.get("diff_scope"),
                "candidate": right.get("diff_scope"),
                "candidate_status": _diff_scope_status(right),
                "passed": _diff_scope_status(right) == "passed",
            },
        }
        task_rows.append(row)
        categories[category].append(row)
    category_rows = {}
    for category, rows in sorted(categories.items()):
        category_rows[category] = {
            "task_count": len(rows),
            "gains": sum(item["transition"] == "improved" for item in rows),
            "regressions": sum(item["transition"] == "regressed" for item in rows),
            "unchanged": sum(item["transition"] == "unchanged" for item in rows),
            "unknown": sum(item["transition"] == "unknown" for item in rows),
            "token_delta": sum(item["token_delta"] for item in rows),
            "tool_call_delta": sum(item["tool_call_delta"] for item in rows),
            "cost_delta": (
                round(sum(float(item["cost_delta"] or 0.0) for item in rows), 8)
                if cost_comparable
                else None
            ),
            "progress": {
                "baseline_patches_created": sum(
                    bool(item["progress"]["baseline"].get("patch_created")) for item in task_rows
                ),
                "candidate_patches_created": sum(
                    bool(item["progress"]["candidate"].get("patch_created")) for item in task_rows
                ),
                "environment_probe_delta": sum(
                    int(item["progress"]["environment_probe_delta"]) for item in task_rows
                ),
                "repeated_command_delta": sum(
                    int(item["progress"]["repeated_command_delta"]) for item in task_rows
                ),
            },
        }
    trace_overhead = _trace_overhead(baseline_tasks, candidate_tasks, event_store)
    decision, rationale = _decision(task_rows)
    return {
        "decision": decision,
        "rationale": rationale,
        "task_results": task_rows,
        "by_failure_category": category_rows,
        "totals": {
            "gains": sum(item["transition"] == "improved" for item in task_rows),
            "regressions": sum(item["transition"] == "regressed" for item in task_rows),
            "token_delta": sum(item["token_delta"] for item in task_rows),
            "tool_call_delta": sum(item["tool_call_delta"] for item in task_rows),
            "latency_ms_delta": sum(item["latency_ms_delta"] for item in task_rows),
            "cost_delta": (
                round(sum(float(item["cost_delta"] or 0.0) for item in task_rows), 8)
                if cost_comparable
                else None
            ),
            "candidate_diff_scope_passed": sum(
                bool(item["diff_scope"]["passed"]) for item in task_rows
            ),
            "candidate_diff_scope_review_required": sum(
                item["diff_scope"]["candidate_status"] == "review_required"
                for item in task_rows
            ),
            "candidate_context_activated": sum(
                _activation_status(item) == "activated" for item in candidate_tasks.values()
            ),
            "candidate_context_abstained": sum(
                _activation_status(item) == "abstained" for item in candidate_tasks.values()
            ),
            "candidate_context_injected": sum(
                bool((item.get("context_activation") or {}).get("injected_context_items"))
                for item in candidate_tasks.values()
            ),
        },
        "trace_overhead": trace_overhead,
        "cost_comparable": cost_comparable,
    }


def _trace_overhead(
    baseline_tasks: Mapping[str, Mapping[str, Any]],
    candidate_tasks: Mapping[str, Mapping[str, Any]],
    store: EventStore,
) -> dict[str, Any]:
    def arm(tasks: Mapping[str, Mapping[str, Any]]) -> tuple[int, int, int]:
        events = [
            event
            for result in tasks.values()
            if result.get("trace_id")
            for event in store.list_events(trace_id=str(result["trace_id"]))
        ]
        return (
            len(events),
            sum(len(event.to_json().encode("utf-8")) for event in events),
            sum(event.type == "CONTEXT_INJECT" for event in events),
        )

    left_count, left_bytes, left_context = arm(baseline_tasks)
    right_count, right_bytes, right_context = arm(candidate_tasks)
    return {
        "instrumentation_mode": "identical_in_both_arms",
        "attributed_to_candidate": False,
        "baseline_event_count": left_count,
        "candidate_event_count": right_count,
        "event_count_delta": right_count - left_count,
        "baseline_serialized_bytes": left_bytes,
        "candidate_serialized_bytes": right_bytes,
        "serialized_bytes_delta": right_bytes - left_bytes,
        "context_inject_event_delta": right_context - left_context,
        "explanation": "Both arms use identical trace instrumentation; deltas include changed agent behavior and the single context event, not tracer latency causality.",
    }


def _decision(rows: list[dict[str, Any]]) -> tuple[str, str]:
    if any(item["transition"] == "regressed" for item in rows):
        return "regress", "At least one task regressed from resolved to unresolved."
    if any(item["transition"] == "improved" for item in rows):
        return "improve", "At least one task improved and no resolved task regressed."
    if any(item["transition"] == "unknown" for item in rows):
        return "inconclusive", "At least one arm lacks an objective evaluator result."
    if rows and all(item["token_delta"] <= 0 and item["tool_call_delta"] <= 0 for item in rows) and any(
        item["token_delta"] < 0 or item["tool_call_delta"] < 0 for item in rows
    ):
        return "improve", "Resolution was unchanged while token/tool usage improved without a task-level efficiency regression."
    return "inconclusive", "Resolution was unchanged and efficiency did not improve monotonically."


def _transition(left: Any, right: Any) -> str:
    if not isinstance(left, bool) or not isinstance(right, bool):
        return "unknown"
    if not left and right:
        return "improved"
    if left and not right:
        return "regressed"
    return "unchanged"


def _diff_scope_status(result: Mapping[str, Any]) -> str:
    scope = result.get("diff_scope")
    if not isinstance(scope, Mapping):
        return "missing"
    return str(scope.get("status") or "missing")


def _activation_status(result: Mapping[str, Any]) -> str:
    activation = result.get("context_activation")
    if not isinstance(activation, Mapping):
        return "not_gated"
    return str(activation.get("status") or "not_gated")


def _tokens(result: Mapping[str, Any]) -> int:
    values = result.get("metrics", {}).get("tokens", {})
    return sum(int(values.get(key, 0)) for key in ("input", "output", "cache"))


def _metric(result: Mapping[str, Any], name: str) -> int:
    return int(result.get("metrics", {}).get(name, 0))


def _metric_float(result: Mapping[str, Any], name: str) -> float:
    return float(result.get("metrics", {}).get(name, 0.0))


def _progress_comparison(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_progress = left.get("metrics", {}).get("progress", {})
    right_progress = right.get("metrics", {}).get("progress", {})
    left_progress = dict(left_progress) if isinstance(left_progress, Mapping) else {}
    right_progress = dict(right_progress) if isinstance(right_progress, Mapping) else {}
    return {
        "baseline": left_progress,
        "candidate": right_progress,
        "environment_probe_delta": int(right_progress.get("environment_probe_count", 0))
        - int(left_progress.get("environment_probe_count", 0)),
        "repeated_command_delta": int(right_progress.get("repeated_command_count", 0))
        - int(left_progress.get("repeated_command_count", 0)),
    }


def _cost_comparable(left: EvalRunManifest, right: EvalRunManifest) -> bool:
    return all(
        manifest.reproducibility.get("model", {}).get("cost_tracking") != "ignore_errors"
        for manifest in (left, right)
    )


def _arm_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eval_run_id": report.get("eval_run_id"),
        "manifest_digest": report.get("manifest_digest"),
        "metrics": dict(report.get("metrics") or {}),
    }


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(dict(value), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        if path.exists():
            if path.read_text(encoding="utf-8") != payload:
                raise EvalSchemaError(f"immutable A/B manifest already exists with different content: {path}")
            return
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
