from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..evolution import EvolutionEngine
from ..store import ExperienceStore
from ..utils import new_id, read_json, utc_now, write_json
from .metrics import score_metric
from .suite import EvalCase, EvalSuite


class EvalRunner:
    """Run JSON eval suites against Praxile proposal generation."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def run(self, suite: EvalSuite) -> dict[str, Any]:
        run_id = new_id("eval")
        cases = [self._run_case(case, suite=suite) for case in suite.cases]
        average = round(sum(float(case["score"]) for case in cases) / len(cases), 4) if cases else 0.0
        passed = all(case["passed"] for case in cases)
        return {
            "schema_version": 1,
            "eval_run_id": run_id,
            "suite": suite.name,
            "suite_path": str(suite.path),
            "created_at": utc_now(),
            "case_count": len(cases),
            "passed": passed,
            "average_score": average,
            "cases": cases,
        }

    def save_report(self, report: dict[str, Any], output: Path | None = None) -> Path:
        path = output or (self.config.paths.state / "experience" / "eval-runs" / f"{report['eval_run_id']}.json")
        write_json(path, report)
        return path

    def _run_case(self, case: EvalCase, *, suite: EvalSuite) -> dict[str, Any]:
        trajectory = self._load_trajectory(case, suite=suite)
        generated = EvolutionEngine(self.config).generate(copy.deepcopy(trajectory))
        metric_results = [score_metric(metric, generated, case.expected) for metric in case.metrics]
        score = round(sum(float(item["score"]) for item in metric_results) / len(metric_results), 4) if metric_results else 0.0
        return {
            "name": case.name,
            "trajectory_id": trajectory.get("task_id"),
            "passed": all(bool(item["passed"]) for item in metric_results),
            "score": score,
            "metrics": metric_results,
            "generated_count": len(generated),
            "generated_proposals": [_compact_proposal(item) for item in generated],
        }

    def _load_trajectory(self, case: EvalCase, *, suite: EvalSuite) -> dict[str, Any]:
        if isinstance(case.input.get("trajectory"), dict):
            return _normalize_trajectory(dict(case.input["trajectory"]), case.name)
        task_id = case.input.get("trajectory_id") or case.input.get("task_id")
        if isinstance(task_id, str) and task_id:
            trajectory = self.store.get_trajectory(task_id)
            if not trajectory:
                raise ValueError(f"eval case `{case.name}` references missing trajectory `{task_id}`")
            return trajectory
        raw_file = case.input.get("trajectory_file")
        if not isinstance(raw_file, str) or not raw_file:
            raise ValueError(f"eval case `{case.name}` requires input.trajectory, input.trajectory_id, or input.trajectory_file")
        path = Path(raw_file)
        if not path.is_absolute():
            candidate = (suite.path.parent / path).resolve()
            path = candidate if candidate.exists() else (self.config.paths.root / raw_file).resolve()
        trajectory = read_json(path, {})
        if not isinstance(trajectory, dict):
            raise ValueError(f"{path}: expected trajectory JSON object")
        return _normalize_trajectory(trajectory, case.name)


def _normalize_trajectory(trajectory: dict[str, Any], case_name: str) -> dict[str, Any]:
    trajectory.setdefault("task_id", f"eval_{case_name.replace(' ', '_')}")
    trajectory.setdefault("user_task", case_name)
    trajectory.setdefault("start_time", utc_now())
    trajectory.setdefault("end_time", utc_now())
    trajectory.setdefault("environment_snapshot", {})
    trajectory.setdefault("actions", [])
    trajectory.setdefault("result", {"status": "completed", "summary": "Eval trajectory."})
    trajectory.setdefault(
        "reward_report",
        {
            "overall": 0.6,
            "should_generate_experience": True,
            "experience_generation": {"should_generate_experience": True, "signals": {"eval": True}},
        },
    )
    return trajectory


def _compact_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id"),
        "type": proposal.get("type"),
        "title": proposal.get("title"),
        "confidence": proposal.get("confidence"),
        "risk_level": proposal.get("risk_level"),
        "target_files": proposal.get("target_files") or [],
    }
