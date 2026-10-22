"""Development cell dispatch, without selection, scoring or promotion."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
from copy import deepcopy

from .five_arm_plan import iter_schedule, plan_digest
from .native_context import owned_candidate_identity
from .owned_native_run import run_owned_native
from .persistent_chat_budget import PersistentChatBudget
from ..utils import write_json


async def execute_cell(plan, cell_id, task, *, directory, wheel, wheel_sha256,
                       model, base_url, credential_file, output_budget,
                       max_steps=50, input_source_root=None,
                       development_candidate_file=None, development_candidate_sha256=None,
                       repository=None, dependency_wheels=None, required_capabilities=None,
                       terminal_selection=None):
    """One exclusive cell, with no automatic replay after uncertain execution.

    output_budget is an explicit additional ceiling, not the plan's total_tokens.
    Frozen study identities remain declarations, not verified model attestations.
    """
    rows = iter_schedule(plan)
    cell = next((row for row in rows if row["cell_id"] == cell_id), None)
    if cell is None:
        raise ValueError("unknown study cell")
    from .five_arm_selection import validate_selection, select_delivered
    terminal_selection = deepcopy(terminal_selection)
    if terminal_selection is not None:
        validate_selection(terminal_selection, plan)
    if task.task_id != cell["task_id"] or task.split != "development":
        raise ValueError("cell requires the matching development task")
    budget = cell["shared_budget"]
    if (type(output_budget) is not int or not 1 <= output_budget <= min(100_000_000, budget["total_tokens"])
            or budget["model_calls"] > 10000 or budget["wall_seconds"] > 3540
            or type(max_steps) is not int or not 1 <= max_steps <= 1000 or credential_file is None):
        raise ValueError("unsupported native cell budget or missing credential broker")
    candidate = {}
    identity = None
    if cell["experience_enabled"]:
        if development_candidate_file is None:
            raise ValueError("experience cell requires a frozen candidate")
        identity = owned_candidate_identity(task, development_candidate_file,
            development_candidate_sha256, repository, development_only=True)
        candidate = {"development_candidate_file": development_candidate_file,
                     "development_candidate_sha256": development_candidate_sha256,
                     "development_only": True, "repository": repository}
    root = Path(directory).absolute()
    # Exclusive creation survives crashes: never silently replay a partial cell.
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    report_path = root / "cell.json"
    report = {"schema_version": "praxile.five_arm_execution.v1", "plan_digest": plan_digest(plan),
        "cell": cell, "task_fingerprint": task.fingerprint, "status": "preparing",
        "output_budget": output_budget, "max_steps": max_steps,
        "executor": {"model": model, "wheel_sha256": wheel_sha256,
                     "image": task.environment.revision,
                     "endpoint_sha256": hashlib.sha256(base_url.encode()).hexdigest()},
        "candidate_identity": identity, "attempts": [], "selected_attempt": None,
        "terminal_selection_policy": terminal_selection,
        "controlled_execution_verified": False, "total_token_bound_verified": False,
        "provenance_verified": False, "promotion_eligible": False}
    write_json(report_path, report)
    started = time.monotonic()
    try:
        quota = PersistentChatBudget.create(root / "quota.sqlite", max_calls=budget["model_calls"],
            output_budget=output_budget, ttl_seconds=budget["wall_seconds"])
        for index in range(cell["max_attempts"]):
            remaining = int(budget["wall_seconds"] - (time.monotonic() - started))
            status = quota.status()
            if remaining < 1 or status["expired"] or status["clock_rollback_detected"] or status["remaining_calls"] < 1 or status["remaining_output"] < 1:
                report["status"] = "budget_exhausted"
                break
            attempt = {"index": index, "status": "dispatching"}
            report["attempts"].append(attempt)
            report["status"] = "running"
            write_json(report_path, report)
            try:
                path = await asyncio.wait_for(run_owned_native(task, state_root=root / f"attempt-{index}",
                    image=task.environment.revision, wheel=wheel, wheel_sha256=wheel_sha256,
                    model=model, base_url=base_url, credential_file=credential_file,
                    input_source_root=input_source_root, max_steps=max_steps, timeout_seconds=remaining,
                    shared_chat_budget=quota, task_local_memory_enabled=cell["task_local_enabled"],
                    dependency_wheels=dependency_wheels, required_capabilities=required_capabilities,
                    **candidate), timeout=remaining)
                path = Path(path)
                if path.is_symlink() or not path.is_file():
                    raise ValueError("missing or unsafe attempt report")
                relative = path.resolve().relative_to((root / f"attempt-{index}").resolve())
                raw = path.read_bytes()
                evidence = json.loads(raw)
                if evidence.get("status") != "executor_returned" or evidence.get("cleanup_verified") is not True:
                    raise ValueError("attempt lacks verified execution cleanup")
                attempt.update(status="returned", report=f"attempt-{index}/" + relative.as_posix(),
                               sha256=hashlib.sha256(raw).hexdigest())
            except BaseException as error:
                attempt.update(status="outcome_unknown", error_type=type(error).__name__)
                raise
            finally:
                report["shared_budget"] = quota.status()
                write_json(report_path, report)
        else:
            report["status"] = "executed_not_scored"
        if terminal_selection is not None:
            report["selection"] = select_delivered(report["attempts"], root, terminal_selection)
            report["selected_attempt"] = report["selection"]["selected_attempt"]
    except BaseException as error:
        report["status"] = "interrupted" if not isinstance(error, Exception) else "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(report_path, report)
    return report_path


async def execute_study(plan, tasks, *, directory, execution, input_source_roots=None):
    """Execute every development cell in frozen order; stop on uncertain failure.

    A separate quota is created per cell. Roles within a cell must share that
    quota; this entry point currently dispatches only executor-side roles.
    """
    from .contracts import BenchmarkTask
    plan = deepcopy(plan)
    # Force validation before claiming any directory, without materializing all cells.
    next(iter_schedule(plan))
    required = {"wheel", "wheel_sha256", "model", "base_url", "credential_file", "output_budget",
                "development_candidate_file", "development_candidate_sha256", "repository"}
    optional = {"max_steps", "dependency_wheels", "required_capabilities", "terminal_selection"}
    if not isinstance(execution, dict) or not required <= execution.keys() or set(execution) - required - optional:
        raise ValueError("study execution requires explicit native options without per-arm overrides")
    options = deepcopy(execution)
    if options.get("terminal_selection") is not None:
        from .five_arm_selection import validate_selection
        validate_selection(options["terminal_selection"], plan)
    tasks = deepcopy(tasks)
    expected = set(plan["spec"]["task_ids"])
    if not isinstance(tasks, dict) or set(tasks) != expected:
        raise ValueError("study requires exactly its declared development tasks")
    roots = dict(input_source_roots or {})
    if set(roots) - expected:
        raise ValueError("input roots contain undeclared tasks")
    identities = {}
    for task_id, task in tasks.items():
        if (not isinstance(task, BenchmarkTask) or task.task_id != task_id or task.split != "development"
                or task.environment.kind != "harbor"):
            raise ValueError("invalid development task binding")
        if bool(task.inputs) != (roots.get(task_id) is not None):
            raise ValueError("public task inputs require a matching source root")
        identities[task_id] = owned_candidate_identity(task, options["development_candidate_file"],
            options["development_candidate_sha256"], options["repository"], development_only=True)
    budget = plan["spec"]["budget"]
    if (type(options["output_budget"]) is not int
            or not 1 <= options["output_budget"] <= min(100_000_000, budget["total_tokens"])
            or budget["model_calls"] > 10000 or budget["wall_seconds"] > 3540
            or type(options.get("max_steps", 50)) is not int or not 1 <= options.get("max_steps", 50) <= 1000
            or options["credential_file"] is None):
        raise ValueError("unsupported native study budget or missing credential broker")
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    write_json(root / "plan.json", plan)
    report_path = root / "study.json"
    report = {"schema_version": "praxile.five_arm_study_execution.v1", "plan_digest": plan_digest(plan),
        "status": "preparing", "planned_cells": len(tasks) * plan["spec"]["repetitions"] * len(plan["arms"]),
        "task_fingerprints": {key: task.fingerprint for key, task in tasks.items()},
        "candidate_identities": identities, "cells": [], "promotion_eligible": False,
        "terminal_selection_policy": options.get("terminal_selection"),
        "controlled_execution_verified": False, "provenance_verified": False,
        "selection_performed": False, "official_evaluation": "not_run"}
    write_json(report_path, report)
    try:
        for index, cell in enumerate(iter_schedule(plan)):
            entry = {"cell_id": cell["cell_id"], "status": "dispatching"}
            report["cells"].append(entry)
            report["status"] = "running"
            write_json(report_path, report)
            try:
                cell_root = root / "cells" / str(index)
                path = await execute_cell(plan, cell["cell_id"], tasks[cell["task_id"]], directory=cell_root,
                    input_source_root=roots.get(cell["task_id"]), **options)
                raw = path.read_bytes()
                result = json.loads(raw)
                if (result.get("plan_digest") != report["plan_digest"] or result.get("cell") != cell
                        or result.get("status") not in {"executed_not_scored", "budget_exhausted"}):
                    raise ValueError("cell result does not match frozen dispatch")
                entry.update(status=result["status"], report=path.relative_to(root).as_posix(),
                             sha256=hashlib.sha256(raw).hexdigest())
                if options.get("terminal_selection") is not None:
                    entry["selection"] = result["selection"]
            except BaseException as error:
                entry.update(status="outcome_unknown", error_type=type(error).__name__)
                raise
            finally:
                write_json(report_path, report)
        report["status"] = "dispatched_not_scored"
        report["selection_performed"] = options.get("terminal_selection") is not None
    except BaseException as error:
        report["status"] = "interrupted" if not isinstance(error, Exception) else "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        write_json(report_path, report)
    return report_path
