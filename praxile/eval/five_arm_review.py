"""Bind a selected cell artifact to a separate judge without feedback to execution."""
from pathlib import Path
import re

from .five_arm_plan import iter_schedule, plan_digest
from .five_arm_selection import validate_selection, select_delivered
from .owned_content_review import _reader, _json, _sha, prepare_owned_review


def validate_selection_binding(value, execution_binding):
    hashes = {"plan_sha256", "cell_sha256", "policy_sha256", "execution_sha256"}
    if (not isinstance(value, dict) or set(value) != hashes | {"selected_attempt"}
            or any(not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{64}", value[key]) for key in hashes)
            or type(value["selected_attempt"]) is not int or value["selected_attempt"] < 0
            or not isinstance(execution_binding, dict)
            or value["execution_sha256"] != execution_binding.get("execution_sha256")):
        raise ValueError("invalid selected execution binding")
    return dict(value)


def prepare_selected_cell_review(plan, cell_id, task, *, cell_directory, cell_sha256,
                                 evaluator_root, source_path, output_path,
                                 source_format="text", output_format="text"):
    expected = next((row for row in iter_schedule(plan) if row["cell_id"] == cell_id), None)
    split = "heldout" if plan["track"] == "heldout_frozen" else "development"
    if expected is None or task.task_id != expected["task_id"] or task.split != split:
        raise ValueError("selected review requires matching cell and protocol split")
    root = Path(cell_directory)
    evaluator_directory = Path(evaluator_root).resolve()
    evaluator_file = (evaluator_directory / task.evaluator.private_ref).resolve()
    if evaluator_directory.is_relative_to(root.resolve()) or evaluator_file.is_relative_to(root.resolve()):
        raise ValueError("judge requirements must be outside all cell executions")
    raw = _reader(root)("cell.json")
    if _sha(raw) != cell_sha256:
        raise ValueError("cell receipt digest mismatch")
    cell = _json(raw)
    if (cell.get("schema_version") != "praxile.five_arm_execution.v1"
            or cell.get("plan_digest") != plan_digest(plan) or cell.get("cell") != expected
            or cell.get("task_fingerprint") != task.fingerprint
            or cell.get("status") not in {"executed_not_scored", "budget_exhausted"}):
        raise ValueError("cell evidence does not match frozen study")
    policy = cell.get("terminal_selection_policy")
    policy_digest = validate_selection(policy, plan)
    attempts = cell.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > expected["max_attempts"]:
        raise ValueError("invalid cell attempts")
    selection = select_delivered(attempts, root, policy)
    if (selection != cell.get("selection") or selection["selected_attempt"] != cell.get("selected_attempt")
            or type(selection["selected_attempt"]) is not type(cell.get("selected_attempt"))):
        raise ValueError("selected attempt differs from frozen rule replay")
    index = selection["selected_attempt"]
    if index is None:
        raise ValueError("no delivered attempt to review")
    report_path = Path(attempts[index]["report"])
    if report_path.name != "execution.json":
        raise ValueError("selected report must be an owned execution report")
    inputs, binding = prepare_owned_review(task, execution_dir=root / report_path.parent,
        evaluator_root=evaluator_root, source_path=source_path, output_path=output_path,
        source_format=source_format, output_format=output_format)
    if binding["execution_sha256"] != attempts[index]["sha256"]:
        raise ValueError("selected execution changed during extraction")
    selected = {"plan_sha256": plan_digest(plan).removeprefix("sha256:"), "cell_sha256": cell_sha256,
        "policy_sha256": policy_digest.removeprefix("sha256:"), "selected_attempt": index,
        "execution_sha256": binding["execution_sha256"]}
    return inputs, binding, validate_selection_binding(selected, binding)


def review_selected_cell(plan, cell_id, task, *, cell_directory, cell_sha256, evaluator_root,
                         source_path, output_path, directory, credential_file, endpoint, model,
                         total_output_tokens, public=False, batch_size=4, timeout_per_call=90,
                         request_options=None, source_format="text", output_format="text", cancel_requested=None):
    from .broker_content_review import run_broker_content_review
    from .persistent_chat_budget import PersistentChatBudget
    if public is not True:
        raise ValueError("sending development requirements and artifacts requires explicit public opt-in")
    if Path(directory).resolve().is_relative_to(Path(cell_directory).resolve()):
        raise ValueError("judge state must be outside execution cell")
    inputs, binding, selection = prepare_selected_cell_review(plan, cell_id, task,
        cell_directory=cell_directory, cell_sha256=cell_sha256, evaluator_root=evaluator_root,
        source_path=source_path, output_path=output_path, source_format=source_format, output_format=output_format)
    quota = PersistentChatBudget(Path(cell_directory) / "quota.sqlite")
    return run_broker_content_review(directory=directory, inputs=inputs, credential_file=credential_file,
        endpoint=endpoint, model=model, shared_budget=quota, total_output_tokens=total_output_tokens,
        public=True, batch_size=batch_size, timeout_per_call=timeout_per_call, request_options=request_options,
        execution_binding=binding, selection_binding=selection, cancel_requested=cancel_requested)
