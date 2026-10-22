"""Development cell dispatch, without selection, scoring or promotion."""
import asyncio
import hashlib
import json
from pathlib import Path
import time

from .five_arm_plan import iter_schedule, plan_digest
from .native_context import owned_candidate_identity
from .owned_native_run import run_owned_native
from .persistent_chat_budget import PersistentChatBudget
from ..utils import write_json


async def execute_cell(plan, cell_id, task, *, directory, wheel, wheel_sha256,
                       model, base_url, credential_file, output_budget,
                       max_steps=50, input_source_root=None,
                       development_candidate_file=None, development_candidate_sha256=None,
                       repository=None, dependency_wheels=None, required_capabilities=None):
    """One exclusive cell, with no automatic replay after uncertain execution.

    output_budget is an explicit additional ceiling, not the plan's total_tokens.
    Frozen study identities remain declarations, not verified model attestations.
    """
    rows = iter_schedule(plan)
    cell = next((row for row in rows if row["cell_id"] == cell_id), None)
    if cell is None:
        raise ValueError("unknown study cell")
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
    except BaseException as error:
        report["status"] = "interrupted" if not isinstance(error, Exception) else "failed"
        report["error_type"] = type(error).__name__
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(report_path, report)
    return report_path
