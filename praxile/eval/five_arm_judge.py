"""Freeze independent development judge configuration before study dispatch."""
from copy import deepcopy
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from .owned_content_review import _reader, _sha, _json
from .v2.schema import canonical_json
from ..model_options import validate_request_options


def judge_identity(judge, tasks):
    required = {"credential_file", "endpoint", "model", "total_output_tokens", "public", "evaluator_root", "artifacts"}
    optional = {"batch_size", "timeout_per_call", "request_options"}
    if not isinstance(judge, dict) or not required <= judge.keys() or set(judge) - required - optional:
        raise ValueError("invalid study judge configuration")
    if judge["public"] is not True or not isinstance(judge["model"], str) or not judge["model"].strip():
        raise ValueError("study judge requires public opt-in and model")
    if not isinstance(judge["endpoint"], str):
        raise ValueError("study judge endpoint must be a string")
    endpoint = urlsplit(judge["endpoint"])
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or not endpoint.path.endswith("/chat/completions")):
        raise ValueError("study judge requires a fixed HTTPS chat endpoint")
    for field, default, upper in (("total_output_tokens", None, 256000), ("batch_size", 4, 32), ("timeout_per_call", 90, 120)):
        value = judge.get(field, default)
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError("invalid study judge budget")
    artifacts = judge["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != set(tasks):
        raise ValueError("judge artifacts must match study tasks")
    bindings = {}
    for task_id, task in tasks.items():
        item = artifacts[task_id]
        if (not isinstance(item, dict) or not {"source_path", "output_path"} <= item.keys()
                or set(item) - {"source_path", "output_path", "source_format", "output_format"}
                or item["source_path"] not in {value.path for value in task.inputs}
                or item["output_path"] not in {value.path for value in task.outputs}
                or any(item.get(key, "text") not in {"text", "docx_body", "pdf_text"} for key in ("source_format", "output_format"))
                or (task.evaluator.name, task.evaluator.version) != ("praxile-content-review", "1")):
            raise ValueError("invalid development judge artifact binding")
        raw = _reader(judge["evaluator_root"])(task.evaluator.private_ref)
        if "sha256:" + _sha(raw) != task.evaluator.content_digest or set(_json(raw)) != {"requirements"}:
            raise ValueError("development requirements differ from frozen task")
        bindings[task_id] = {**deepcopy(item), "evaluator_sha256": _sha(raw)}
    return {"schema_version": "praxile.five_arm_judge.v1", "model": judge["model"],
        "endpoint_sha256": _sha(judge["endpoint"].encode()), "total_output_tokens": judge["total_output_tokens"],
        "batch_size": judge.get("batch_size", 4), "timeout_per_call": judge.get("timeout_per_call", 90),
        "request_options": validate_request_options(judge.get("request_options", {})), "artifacts": bindings,
        "protocol": "broker_content_review_units_v1", "official_evaluation": False}


def validate_study_judge(judge, tasks, plan, directory):
    identity = judge_identity(judge, tasks)
    digest = "sha256:" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    if plan["spec"]["identities"]["judge"] != digest:
        raise ValueError("judge differs from frozen study identity")
    root = Path(directory).resolve()
    evaluator = Path(judge["evaluator_root"]).resolve()
    if evaluator.is_relative_to(root) or any((evaluator / task.evaluator.private_ref).resolve().is_relative_to(root) for task in tasks.values()):
        raise ValueError("judge requirements must be outside the study")
    return identity
