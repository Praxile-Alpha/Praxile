from __future__ import annotations

from typing import Any

from ..config import Config
from ..utils import read_json, utc_now, write_json


DEFAULT_WORKFLOWS: dict[str, dict[str, Any]] = {
    "test-failure-repair": {
        "name": "test-failure-repair",
        "requires_spec": "optional",
        "allowed_tools": ["read_file", "search", "edit_file", "run_test", "run_command"],
        "required_tests": ["failing test first", "targeted regression test"],
        "proposal_gate": "Generate failure_pattern or eval proposal only when fix evidence is specific.",
        "silent_failure_rules": ["tests_detected_not_run", "no_regression_after_edit"],
        "audit_outputs": ["trajectory", "reward", "diff", "test_output"],
        "review_strategy": "Accept narrow eval/failure learning; inspect broad harness rules.",
    },
    "spec-driven-feature": {
        "name": "spec-driven-feature",
        "requires_spec": "required",
        "allowed_tools": ["read_file", "search", "edit_file", "run_test", "run_command"],
        "required_tests": ["acceptance tests", "regression suite"],
        "proposal_gate": "Suppress durable memory if spec compliance is partial or failed.",
        "silent_failure_rules": ["missing_acceptance_criteria", "scope_drift", "tests_detected_not_run"],
        "audit_outputs": ["spec_compliance", "reward", "proposal_gate"],
        "review_strategy": "Review spec deltas before accepting new skills or patterns.",
    },
    "architecture-change": {
        "name": "architecture-change",
        "requires_spec": "required",
        "allowed_tools": ["read_file", "search", "project_map", "run_test"],
        "required_tests": ["contract tests", "migration validation", "rollback validation"],
        "proposal_gate": "Architecture gate proposal must be accepted before implementation proceeds as normal patch work.",
        "silent_failure_rules": ["architecture_gate_required", "broad_edit_without_boundary", "missing_rollback_plan"],
        "audit_outputs": ["architecture_gate", "impact_scope", "rollback_plan", "validation_strategy"],
        "review_strategy": "Human review required; no batch accept.",
    },
    "security-fix": {
        "name": "security-fix",
        "requires_spec": "recommended",
        "allowed_tools": ["read_file", "search", "edit_file", "run_test", "run_command"],
        "required_tests": ["security regression", "negative case"],
        "proposal_gate": "High-risk by default; durable rules require evidence and anti-scope.",
        "silent_failure_rules": ["sensitive_path_touch", "missing_negative_test", "unsafe_command"],
        "audit_outputs": ["safety_decisions", "diff", "test_output"],
        "review_strategy": "Inspect all security rules and frozen boundaries.",
    },
    "migration": {
        "name": "migration",
        "requires_spec": "required",
        "allowed_tools": ["read_file", "search", "edit_file", "run_test", "run_command"],
        "required_tests": ["forward migration", "rollback or restore path", "data contract checks"],
        "proposal_gate": "Frozen boundary or harness rule proposal recommended for repeated migration constraints.",
        "silent_failure_rules": ["missing_rollback_plan", "storage_contract_change", "broad_edit_without_boundary"],
        "audit_outputs": ["migration_plan", "diff", "validation_strategy"],
        "review_strategy": "Require human review for storage/auth/session contract changes.",
    },
}


class WorkflowService:
    def __init__(self, config: Config):
        self.config = config

    def list(self) -> dict[str, Any]:
        workflows = []
        effective = self._effective_workflows()
        for name, workflow in sorted(effective["workflows"].items()):
            workflows.append(
                {
                    "name": name,
                    "source": self._source_for(name),
                    "requires_spec": workflow.get("requires_spec"),
                    "allowed_tools": workflow.get("allowed_tools", []),
                    "required_tests": workflow.get("required_tests", []),
                    "proposal_gate": workflow.get("proposal_gate"),
                }
            )
        return {
            "generated_at": utc_now(),
            "workflow_root": self._workflow_dir().relative_to(self.config.paths.root).as_posix(),
            "workflows": workflows,
            "errors": effective["errors"],
        }

    def show(self, name: str) -> dict[str, Any]:
        effective = self._effective_workflows()
        workflows = effective["workflows"]
        if name not in workflows:
            raise KeyError(f"workflow not found: {name}")
        return {"generated_at": utc_now(), "name": name, "source": self._source_for(name), "workflow": workflows[name], "errors": effective["errors"]}

    def seed(self, *, overwrite: bool = False) -> list[str]:
        written: list[str] = []
        root = self._workflow_dir()
        for name, workflow in DEFAULT_WORKFLOWS.items():
            path = root / f"{name}.json"
            if path.exists() and not overwrite:
                continue
            payload = {**workflow, "generated_by": "praxile workflow seed", "created_at": utc_now()}
            write_json(path, payload)
            written.append(path.relative_to(self.config.paths.root).as_posix())
        return written

    def _effective_workflows(self) -> dict[str, Any]:
        workflows = {name: dict(value) for name, value in DEFAULT_WORKFLOWS.items()}
        errors: list[dict[str, str]] = []
        root = self._workflow_dir()
        if root.exists():
            for path in sorted(root.glob("*.json")):
                try:
                    data = read_json(path, {})
                except (OSError, ValueError) as exc:
                    errors.append(
                        {
                            "path": path.relative_to(self.config.paths.root).as_posix(),
                            "error": f"{exc.__class__.__name__}: {exc}",
                        }
                    )
                    continue
                if isinstance(data, dict):
                    name = str(data.get("name") or path.stem)
                    workflows[name] = {**workflows.get(name, {}), **data}
        return {"workflows": workflows, "errors": errors}

    def _source_for(self, name: str) -> str:
        return "file" if (self._workflow_dir() / f"{name}.json").exists() else "builtin"

    def _workflow_dir(self):
        return self.config.paths.state / "workflows"
