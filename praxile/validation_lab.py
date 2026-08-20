from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .config import Config
from .eval import EvalRunner, EvalSuite
from .harness_components import HarnessComponentRegistry
from .store import ExperienceStore
from .utils import file_lock, new_id, path_is_relative_to, utc_now, write_json
from .workspace import WorkspaceManager


VALIDATION_STATES = {
    "proposed",
    "shadow_running",
    "validated",
    "inconclusive",
    "regressed",
    "accepted",
    "rolled_back",
}


class ProposalValidationLab:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.registry = HarnessComponentRegistry(config)

    def validate(self, proposal: dict[str, Any], suite: EvalSuite, *, keep_workspaces: bool | None = None) -> dict[str, Any]:
        component_change = self.registry.validate_proposal(proposal)
        if component_change is None:
            raise ValueError("Proposal validation lab only accepts registered harness component proposals")
        active_version = self.registry.describe(str(component_change["component_id"]))["version"]
        if active_version != component_change.get("base_version"):
            raise ValueError(
                f"Proposal base_version is stale: expected active {active_version}, got {component_change.get('base_version')}"
            )
        self._validate_suite_ownership(suite)
        validation_id = new_id("validation")
        keep = bool(self.config.get("proposal_validation", "keep_workspaces", default=False)) if keep_workspaces is None else keep_workspaces
        manager = WorkspaceManager(self.config)
        baseline = candidate = None
        self._transition(proposal, "shadow_running", reason=f"validation {validation_id} started")
        try:
            baseline = manager.create(mode="copy", label=f"{validation_id}:baseline")
            candidate = manager.create(mode="copy", label=f"{validation_id}:candidate")
            self._apply_candidate_changes(proposal, candidate.root)
            baseline_report = self._run_suite_in_workspace(suite, baseline.root)
            candidate_report = self._run_suite_in_workspace(suite, candidate.root)
            comparison = self._compare(baseline_report, candidate_report)
            status = comparison["decision"]
            report = {
                "schema_version": 1,
                "validation_id": validation_id,
                "proposal_id": proposal.get("proposal_id"),
                "component_change": component_change,
                "status": status,
                "created_at": utc_now(),
                "suite": {
                    "name": suite.name,
                    "path": str(suite.path),
                    "owner": suite.owner,
                    "expected_owner": suite.expected_owner,
                    "case_ownership": [
                        {"name": case.name, "set_type": case.set_type, "owner": case.owner, "expected_owner": case.expected_owner}
                        for case in suite.cases
                    ],
                },
                "isolation": {
                    "mode": "copy",
                    "level": "project_state_isolation",
                    "os_sandbox": False,
                    "commands_checked_by_safety_policy": True,
                    "active_harness_modified": False,
                    "baseline_workspace_id": baseline.workspace_id,
                    "candidate_workspace_id": candidate.workspace_id,
                },
                "baseline": baseline_report,
                "candidate": candidate_report,
                "comparison": comparison,
            }
            report_path = self._save_report(report)
            from .bounded_evolution import BoundedHarnessEvolution
            report["experiment"] = BoundedHarnessEvolution(self.config, self.store).record_experiment(proposal, report)
            write_json(report_path, report)
            proposal["validation"] = {
                "validation_id": validation_id,
                "status": status,
                "report_path": str(report_path.relative_to(self.config.paths.root)),
                "comparison": comparison,
                "experiment": report["experiment"],
            }
            self._transition(proposal, status, reason=comparison["summary"])
            return report
        except Exception as exc:
            report = {
                "schema_version": 1,
                "validation_id": validation_id,
                "proposal_id": proposal.get("proposal_id"),
                "component_change": component_change,
                "status": "inconclusive",
                "created_at": utc_now(),
                "error": f"{exc.__class__.__name__}: {exc}",
                "isolation": {
                    "mode": "copy",
                    "level": "project_state_isolation",
                    "os_sandbox": False,
                    "commands_checked_by_safety_policy": True,
                    "active_harness_modified": False,
                },
            }
            report_path = self._save_report(report)
            from .bounded_evolution import BoundedHarnessEvolution
            report["experiment"] = BoundedHarnessEvolution(self.config, self.store).record_experiment(proposal, report)
            write_json(report_path, report)
            proposal["validation"] = {
                "validation_id": validation_id,
                "status": "inconclusive",
                "report_path": str(report_path.relative_to(self.config.paths.root)),
                "error": report["error"],
                "experiment": report["experiment"],
            }
            self._transition(proposal, "inconclusive", reason=report["error"])
            return report
        finally:
            if not keep:
                for workspace in [baseline, candidate]:
                    if workspace is not None:
                        manager.remove(workspace.workspace_id)

    def _run_suite_in_workspace(self, suite: EvalSuite, root: Path) -> dict[str, Any]:
        workspace_config = Config.load(root)
        workspace_store = ExperienceStore(workspace_config.paths)
        workspace_store.initialize(workspace_config)
        runner = EvalRunner(workspace_config, workspace_store)
        public_suite = EvalSuite(
            name=suite.name,
            path=suite.path,
            owner=suite.owner,
            expected_owner=suite.expected_owner,
            cases=[
                type(case)(
                    name=case.name,
                    input=copy.deepcopy(case.input),
                    expected={},
                    metrics=list(case.metrics),
                    set_type=case.set_type,
                    owner=case.owner,
                    expected_owner=case.expected_owner,
                )
                for case in suite.cases
            ],
        )
        executions = runner.execute(public_suite)
        cases = runner.score_executions(suite, executions)
        average = round(sum(float(case["score"]) for case in cases) / len(cases), 4) if cases else 0.0
        generated_count = sum(int(case.get("generated_count") or 0) for case in cases)
        return {
            "case_count": len(cases),
            "passed": all(bool(case["passed"]) for case in cases),
            "average_score": average,
            "cases": cases,
            "metrics": self._aggregate_metrics(cases),
            "dimensions": {
                "task_success": round(sum(1 for case in cases if case.get("passed")) / max(1, len(cases)), 4),
                "safety": round(sum(1 for case in cases if case.get("passed")) / max(1, len(cases)), 4),
                "regression": _set_pass_rate(cases, "regression"),
                "cost": float(generated_count),
                "human_review_burden": float(generated_count),
            },
        }

    def _apply_candidate_changes(self, proposal: dict[str, Any], workspace_root: Path) -> None:
        state_root = (workspace_root / ".praxile").resolve()
        for change in proposal.get("changes") or []:
            if not isinstance(change, dict):
                raise ValueError("Proposal changes must be objects")
            operation = str(change.get("operation") or "write")
            if operation not in {"write", "append"}:
                raise ValueError(f"Shadow validation does not support operation: {operation}")
            relative = str(change.get("path") or "")
            if relative.startswith("evals/sealed/") or relative.startswith("evals/scorers/"):
                raise PermissionError("Proposal candidates cannot modify sealed evals or scorers")
            target = (state_root / relative).resolve(strict=False)
            if not path_is_relative_to(target, state_root):
                raise PermissionError(f"Candidate path escapes isolated state: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            content = str(change.get("content") or "")
            after = before.rstrip() + "\n\n" + content.rstrip() + "\n" if operation == "append" else content
            target.write_text(after, encoding="utf-8")

    def _compare(self, baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        baseline_score = float(baseline.get("average_score") or 0.0)
        candidate_score = float(candidate.get("average_score") or 0.0)
        delta = round(candidate_score - baseline_score, 4)
        minimum = float(self.config.get("proposal_validation", "minimum_improvement", default=0.01) or 0.01)
        tolerance = float(self.config.get("proposal_validation", "regression_tolerance", default=0.0) or 0.0)
        set_deltas: dict[str, float] = {}
        for set_type in ["source", "regression", "sealed"]:
            left = _set_score(baseline.get("cases") or [], set_type)
            right = _set_score(candidate.get("cases") or [], set_type)
            if left is not None and right is not None:
                set_deltas[set_type] = round(right - left, 4)
        regressed_sets = [name for name, value in set_deltas.items() if name in {"regression", "sealed"} and value < -tolerance]
        changed_cases = [
            right.get("name")
            for left, right in zip(baseline.get("cases") or [], candidate.get("cases") or [])
            if left.get("score") != right.get("score") or left.get("passed") != right.get("passed")
        ]
        if regressed_sets or delta < -tolerance:
            decision = "regressed"
            summary = f"Candidate regressed: sets={regressed_sets or ['overall']} delta={delta}."
        elif delta >= minimum and changed_cases:
            decision = "validated"
            summary = f"Candidate improved average score by {delta} without protected-set regression."
        else:
            decision = "inconclusive"
            summary = "Candidate did not produce enough comparison evidence to claim improvement."
        return {
            "decision": decision,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "score_delta": delta,
            "set_deltas": set_deltas,
            "changed_cases": changed_cases,
            "regressed_sets": regressed_sets,
            "dimensions": {
                "task_success": _metric_delta(baseline, candidate, "task_success"),
                "safety": _metric_delta(baseline, candidate, "safety"),
                "regressions": _metric_delta(baseline, candidate, "regression"),
                "cost": _metric_delta(baseline, candidate, "cost"),
                "latency_ms": _latency_delta(baseline, candidate),
                "human_review_burden": _metric_delta(baseline, candidate, "human_review_burden"),
            },
            "summary": summary,
        }

    def _transition(self, proposal: dict[str, Any], status: str, *, reason: str) -> None:
        if status not in VALIDATION_STATES:
            raise ValueError(f"Unsupported validation state: {status}")
        proposal["status"] = status
        proposal.setdefault("lifecycle_events", []).append({"status": status, "created_at": utc_now(), "reason": reason})
        self.store.write_proposal(proposal)

    def _save_report(self, report: dict[str, Any]) -> Path:
        path = self.config.paths.state / "experience" / "validations" / f"{report['validation_id']}.json"
        with file_lock(self.config.paths.state / "experience" / "validations.lock"):
            write_json(path, report)
        return path

    def _validate_suite_ownership(self, suite: EvalSuite) -> None:
        if suite.expected_owner == "proposal_composer":
            raise ValueError("Eval expected outputs cannot be owned by proposal_composer")
        for case in suite.cases:
            if case.set_type == "sealed" and case.expected_owner == "proposal_composer":
                raise ValueError(f"Sealed case `{case.name}` cannot be scored by proposal_composer ownership")

    @staticmethod
    def _aggregate_metrics(cases: list[dict[str, Any]]) -> dict[str, float]:
        values: dict[str, list[float]] = {}
        for case in cases:
            for metric in case.get("metrics") or []:
                values.setdefault(str(metric.get("metric") or "unknown"), []).append(float(metric.get("score") or 0.0))
        return {name: round(sum(items) / len(items), 4) for name, items in values.items() if items}


def _set_score(cases: list[dict[str, Any]], set_type: str) -> float | None:
    values = [float(case.get("score") or 0.0) for case in cases if case.get("set_type") == set_type]
    return round(sum(values) / len(values), 4) if values else None


def _metric_delta(baseline: dict[str, Any], candidate: dict[str, Any], name: str) -> float | None:
    left = (baseline.get("dimensions") or {}).get(name)
    right = (candidate.get("dimensions") or {}).get(name)
    return round(float(right) - float(left), 4) if left is not None and right is not None else None


def _latency_delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    def total(report: dict[str, Any]) -> float:
        return sum(float((case.get("observation") or {}).get("latency_ms") or 0.0) for case in report.get("cases") or [])

    left = total(baseline)
    right = total(candidate)
    return round(right - left, 2) if left or right else None


def _set_pass_rate(cases: list[dict[str, Any]], set_type: str) -> float | None:
    selected = [case for case in cases if case.get("set_type") == set_type]
    return round(sum(1 for case in selected if case.get("passed")) / len(selected), 4) if selected else None
