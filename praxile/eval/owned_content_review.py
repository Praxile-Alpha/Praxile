"""Bind an independent development judge to frozen native text artifacts.

Local consistency is checked, not authenticity against a malicious host. This
is not an official benchmark evaluator or an automatic promotion decision.
"""
import hashlib
import json
from pathlib import Path

from .artifact_read import read_artifact
from .content_review_run import run_content_review
from .contracts import BenchmarkTask


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate evidence key")
            value[key] = item
        return value
    def reject(_):
        raise ValueError("nonfinite evidence value")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)
    except RecursionError as error:
        raise ValueError("evidence nesting exceeds limit") from error
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value


def _reader(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("evidence root must be a directory, not a link")
    info = root.stat()
    def read(path):
        return read_artifact(root, path, root_identity=(info.st_dev, info.st_ino), max_bytes=1024**2)
    return read


def prepare_owned_review(task: BenchmarkTask, *, execution_dir: Path, evaluator_root: Path,
                         source_path: str, output_path: str, output_format="text"):
    if not isinstance(task, BenchmarkTask):
        raise ValueError("typed benchmark task required")
    if (task.evaluator.name, task.evaluator.version) != ("praxile-content-review", "1"):
        raise ValueError("this bridge supports only explicit development content-review evaluators")
    if Path(evaluator_root).resolve().is_relative_to(Path(execution_dir).resolve()):
        raise ValueError("private evaluator storage must be outside the execution directory")
    source = next((item for item in task.inputs if item.path == source_path), None)
    output = next((item for item in task.outputs if item.path == output_path), None)
    if source is None or output is None:
        raise ValueError("source and output must be declared in the frozen task")
    if output_format not in {"text", "docx_body"}:
        raise ValueError("unsupported output extraction profile")
    if output_format == "docx_body" and output.media_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raise ValueError("DOCX extraction requires declared DOCX media type")
    if output_format == "text" and not (output.media_type.startswith("text/") or output.media_type == "application/json"):
        raise ValueError("binary output requires a separately verified extraction pipeline")
    read = _reader(execution_dir)
    report_raw = read("execution.json")
    report = _json(report_raw)
    if (report.get("schema_version") != "praxile.owned_native_execution.v1"
            or report.get("task_id") != task.task_id or report.get("task_fingerprint") != task.fingerprint
            or report.get("image") != task.environment.revision
            or report.get("status") != "executor_returned" or report.get("cleanup_verified") is not True
            or report.get("required_outputs_collected") is not True):
        raise ValueError("native execution is incomplete or belongs to another task")
    expected_inputs = [{"path": "inputs/" + item.path, "content_digest": item.content_digest} for item in task.inputs]
    if report.get("public_inputs") != expected_inputs:
        raise ValueError("public input binding mismatch")
    records = report.get("outputs")
    if (not isinstance(records, list) or len(records) != len(task.outputs)
            or any(not isinstance(item, dict) or not isinstance(item.get("path"), str) for item in records)
            or {item.get("path") for item in records} != {item.path for item in task.outputs}):
        raise ValueError("output contract records mismatch")
    for contract in task.outputs:
        record = next(item for item in records if item["path"] == contract.path)
        if record.get("required") is not contract.required or (contract.required and record.get("status") != "collected"):
            raise ValueError("required output not collected")
    record = next(item for item in records if item["path"] == output_path)
    if record.get("status") != "collected":
        raise ValueError("selected output not collected")
    output_raw = read("outputs/" + output_path)
    source_raw = read("inputs/" + source_path)
    trajectory_raw = read("agent/praxile-result.json")
    if (record.get("sha256") != _sha(output_raw) or type(record.get("bytes")) is not int
            or record["bytes"] != len(output_raw) or source.content_digest != "sha256:" + _sha(source_raw)
            or report.get("source_result_sha256") != _sha(trajectory_raw)):
        raise ValueError("execution artifact digest mismatch")
    if _json(trajectory_raw).get("schema_version") != "praxile.native_benchmark_run.v1":
        raise ValueError("unsupported native trajectory")
    evaluator_raw = _reader(evaluator_root)(task.evaluator.private_ref)
    if task.evaluator.content_digest != "sha256:" + _sha(evaluator_raw):
        raise ValueError("private evaluator digest mismatch")
    evaluator = _json(evaluator_raw)
    if set(evaluator) != {"requirements"} or not isinstance(evaluator["requirements"], list):
        raise ValueError("private evaluator requires a requirements array only")
    extraction = None
    if output_format == "docx_body":
        from .document_review_extraction import extract_docx_body
        output_text, extraction = extract_docx_body(output_raw)
    else:
        output_text = output_raw.decode("utf-8")
    inputs = {"source_text": source_raw.decode("utf-8"), "output_text": output_text,
              "requirements": evaluator["requirements"]}
    binding = {"task_fingerprint": task.fingerprint.removeprefix("sha256:"),
        "execution_sha256": _sha(report_raw), "trajectory_sha256": _sha(trajectory_raw),
        "source_sha256": _sha(source_raw), "output_sha256": _sha(output_raw),
        "evaluator_sha256": _sha(evaluator_raw)}
    if extraction is not None:
        binding["output_extraction"] = extraction
    return inputs, binding


def review_owned_output(config, task, *, execution_dir, evaluator_root, source_path, output_path,
                        run_id, total_output_tokens, batch_size=4, private=True, timeout_per_call=90,
                        output_format="text"):
    inputs, binding = prepare_owned_review(task, execution_dir=execution_dir, evaluator_root=evaluator_root,
                                          source_path=source_path, output_path=output_path, output_format=output_format)
    return run_content_review(config, run_id=run_id, inputs=inputs, execution_binding=binding,
        total_output_tokens=total_output_tokens, batch_size=batch_size, private=private,
        timeout_per_call=timeout_per_call)
