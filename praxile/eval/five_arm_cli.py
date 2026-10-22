"""Preflight a frozen five-arm study; execution requires --execute."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

from .five_arm_execution import execute_study, prepare_study
from .five_arm_plan import plan_digest
from .owned_content_review import _json, _reader
from .owned_native_cli import load_task
from .worker_preflight import check_worker_wheel
from .worker_requirements import validate_requirements


def _load(path):
    return _json(_reader(path.parent)(path.name))


def _paths(options, fields):
    for field in fields:
        value = options.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("configuration requires nonempty local paths")
        options[field] = Path(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--task", type=Path, action="append", required=True)
    parser.add_argument("--execution-config", type=Path, required=True)
    parser.add_argument("--judge-config", type=Path)
    parser.add_argument("--input-roots", type=Path, help="JSON object mapping task IDs to public input directories")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Start Docker and potentially billable model/judge calls")
    args = parser.parse_args(argv)
    try:
        plan = _load(args.plan)
        tasks = {}
        for path in args.task:
            task = load_task(path)
            if task.task_id in tasks:
                raise ValueError("duplicate study task")
            tasks[task.task_id] = task
        execution = _load(args.execution_config)
        _paths(execution, ("wheel", "credential_file", "development_candidate_file"))
        judge = _load(args.judge_config) if args.judge_config else None
        if judge is not None:
            _paths(judge, ("credential_file", "evaluator_root"))
        roots = _load(args.input_roots) if args.input_roots else {}
        _paths(roots, tuple(roots))
        prepared = prepare_study(plan, tasks, directory=args.output, execution=execution,
                                 input_source_roots=roots, judge=judge)
        # Offline package checks do not import the worker or start its environment.
        check_worker_wheel(execution["wheel"], execution["wheel_sha256"])
        dependencies, capabilities = validate_requirements(execution.get("dependency_wheels"),
            execution.get("required_capabilities"), worker_filename=execution["wheel"].name)
        execution.update(dependency_wheels=dependencies, required_capabilities=capabilities)
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("study output already exists; replay is forbidden")
        print(json.dumps({"plan_digest": plan_digest(prepared["plan"]),
            "track": plan["track"],
            "task_count": len(tasks), "planned_cells": len(tasks) * plan["spec"]["repetitions"] * len(plan["arms"]),
            "budget_per_cell": plan["spec"]["budget"], "output_budget_per_cell": execution["output_budget"],
            "independent_judge_configured": judge is not None, "execute": args.execute,
            "promotion_eligible": False, "official_evaluation": "not_run"}))
        if not args.execute:
            print("Plan only: no credentials read, quota allocated, Docker or model calls. "
                  "Infrastructure, public inputs and remote availability remain unverified.")
            return 0
        report_path = asyncio.run(execute_study(plan, tasks, directory=args.output,
            execution=execution, input_source_roots=roots, judge=judge))
        report = _load(report_path)
        print("Dispatch returned; inspect study.json and run the offline audit. "
              "This is not task success or promotion approval.")
        return 0 if report.get("status") == "dispatched_not_scored" else 1
    except KeyboardInterrupt:
        print("Interrupted; inspect persisted state before another run. No automatic replay.", file=sys.stderr)
        return 130
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ImportError, sqlite3.Error) as error:
        # Provider errors and configuration values may contain credentials or task text.
        print("Study rejected or failed: " + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
