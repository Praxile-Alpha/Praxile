"""Post-hoc independent content review, isolated from online learning state."""
from copy import deepcopy
import json
from pathlib import Path

from ..utils import write_json
from .broker_content_review import run_broker_content_review
from .five_arm_judge import judge_identity
from .online_stream import OnlineStream
from .owned_content_review import _reader, _json, _sha, prepare_owned_review
from .persistent_chat_budget import PersistentChatBudget


def prepare_online_review(stream_directory, index, *, judge, directory):
    return _prepare_online_review(stream_directory, index, judge=judge, directory=directory, require_new=True)


def _prepare_online_review(stream_directory, index, *, judge, directory, require_new):
    target = OnlineStream(stream_directory).review_target(index)
    task = target["task"]
    judge = deepcopy(judge)
    identity = judge_identity(judge, {task.task_id: task})
    frozen = target["frozen_judge_identity"]
    if frozen is not None:
        expected = {**frozen, "artifacts": {task.task_id: frozen["artifacts"][task.task_id]}}
        if identity != expected:
            raise ValueError("judge differs from precommitted online review protocol")
    stream = Path(stream_directory).resolve()
    root = Path(directory).absolute()
    evaluator = Path(judge["evaluator_root"]).resolve()
    if root.resolve() != root or (require_new and root.exists()):
        raise ValueError("review directory must be free of symlinks and new for execution")
    if (root.is_relative_to(stream) or evaluator.is_relative_to(stream)
            or (evaluator / task.evaluator.private_ref).resolve().is_relative_to(stream)):
        raise ValueError("review state and evaluator must be outside the online stream")
    inputs, binding = prepare_owned_review(task, execution_dir=target["execution_dir"],
        evaluator_root=evaluator, **judge["artifacts"][task.task_id])
    if binding["execution_sha256"] != target["execution_sha256"]:
        raise ValueError("review execution differs from completed stream evidence")
    receipt = {"schema_version": "praxile.online_content_review.v1", "track": "online_stream",
        "task_id": task.task_id, "task_index": index, "task_fingerprint": task.fingerprint,
        "completion_event_sha256": target["completion_event_sha256"], "execution_binding": binding,
        "judge_identity": identity, "official_evaluation": False, "promotion_eligible": False,
        "shared_stream_budget": target["shared_stream_budget"],
        "judge_precommitted_before_execution": frozen is not None, "learning_feedback_allowed": False}
    return {"root": root, "inputs": inputs, "binding": binding, "receipt": receipt, "judge": judge}


def run_online_review(stream_directory, index, *, judge, directory):
    prepared = prepare_online_review(stream_directory, index, judge=judge, directory=directory)
    root, receipt, judge = prepared["root"], prepared["receipt"], prepared["judge"]
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    receipt.update(status="outcome_unknown", automatic_replay_allowed=False)
    write_json(root / "receipt.json", receipt)
    try:
        identity = receipt["judge_identity"]
        requirement_count = len(prepared["inputs"]["requirements"])
        calls = (requirement_count + identity["batch_size"] - 1) // identity["batch_size"]
        quota = OnlineStream(stream_directory).shared_budget()
        if quota is None:
            quota = PersistentChatBudget.create(root / "quota.sqlite", max_calls=calls,
                output_budget=identity["total_output_tokens"],
                ttl_seconds=min(3600, calls * identity["timeout_per_call"] + 30))
        report = run_broker_content_review(directory=root / "judge", inputs=prepared["inputs"],
            execution_binding=prepared["binding"], credential_file=judge["credential_file"],
            endpoint=judge["endpoint"], model=judge["model"], shared_budget=quota, public=True,
            total_output_tokens=identity["total_output_tokens"], batch_size=identity["batch_size"],
            timeout_per_call=identity["timeout_per_call"], request_options=identity["request_options"])
        read = _reader(root / "judge")
        files = {name: read(name, max_bytes=16 * 1024**2) for name in
                 ("report.json", "manifest.json", "trajectory.json", "inputs.json", "broker-usage.json")}
        from .content_review_compare import _checked
        _, verdicts = _checked(_json(files["report.json"]))
        receipt.update(status=_json(files["report.json"])["status"],
                       evidence_sha256={name: _sha(raw) for name, raw in files.items()},
                       report=str(Path(report).relative_to(root)),
                       metric="llm_declared_requirement_preservation",
                       verdict_counts=None if verdicts is None else {label: list(verdicts.values()).count(label)
                           for label in ("preserved", "missing", "contradicted", "uncertain")})
    except BaseException as error:
        receipt["error_type"] = type(error).__name__
        raise
    finally:
        write_json(root / "receipt.json", receipt)
    return root / "receipt.json"


def audit_online_review(stream_directory, index, *, judge, directory, receipt_sha256):
    """Verify local evidence against a caller-pinned receipt, without model calls.

    This checks consistency, not authenticity against a malicious local owner or
    the semantic correctness of the judge's labels.
    """
    from .content_review_batch import plan_content_review_batch
    from .content_review_compare import _checked
    prepared = _prepare_online_review(stream_directory, index, judge=judge,
                                     directory=directory, require_new=False)
    read = _reader(prepared["root"])
    raw = read("receipt.json", max_bytes=16 * 1024**2)
    if _sha(raw) != receipt_sha256:
        raise ValueError("online review receipt digest mismatch")
    receipt = _json(raw)
    receipt.setdefault("shared_stream_budget", False)
    if any(receipt.get(key) != value for key, value in prepared["receipt"].items()):
        raise ValueError("online review receipt differs from stream and judge binding")
    names = {"report.json", "manifest.json", "trajectory.json", "inputs.json", "broker-usage.json"}
    evidence = receipt.get("evidence_sha256")
    if (not isinstance(evidence, dict) or set(evidence) != names
            or receipt.get("report") != "judge/report.json"
            or receipt.get("automatic_replay_allowed") is not False):
        raise ValueError("online review lacks complete evidence")
    files = {}
    for name, digest in evidence.items():
        data = read("judge/" + name, max_bytes=16 * 1024**2)
        if _sha(data) != digest:
            raise ValueError("online review evidence changed")
        files[name] = _json(data)
    manifest, report, trajectory = (files[name] for name in ("manifest.json", "report.json", "trajectory.json"))
    identity = receipt["judge_identity"]
    if (manifest.get("schema_version") != "praxile.broker_content_review.v1"
            or files["inputs.json"] != prepared["inputs"]
            or manifest.get("execution_binding") != prepared["binding"]
            or manifest.get("selection_binding") is not None
            or any(manifest.get(key) != identity[key] for key in ("model", "endpoint_sha256", "request_options"))):
        raise ValueError("online review artifact or judge mismatch")
    expected = plan_content_review_batch(**prepared["inputs"], total_output_tokens=identity["total_output_tokens"],
        batch_size=identity["batch_size"], timeout_per_call=identity["timeout_per_call"],
        private=False, response_mode="function", citation_mode="units")
    plan, verdicts = _checked(report)
    counts = None if verdicts is None else {label: list(verdicts.values()).count(label)
        for label in ("preserved", "missing", "contradicted", "uncertain")}
    if (plan != expected or manifest.get("plan") != expected
            or trajectory.get("content_review_batches") != [report]
            or trajectory.get("content_reviews") != report.get("groups")
            or receipt.get("status") != report.get("status") or receipt.get("verdict_counts") != counts
            or receipt.get("metric") != "llm_declared_requirement_preservation"):
        raise ValueError("online review plan, trajectory or counts mismatch")
    return {"schema_version": "praxile.online_review_audit.v1", "track": "online_stream",
        "judge_protocol_sha256": _sha(json.dumps(
            {key: value for key, value in identity.items() if key != "artifacts"}, sort_keys=True).encode()),
        "task_index": index, "task_id": receipt["task_id"], "receipt_sha256": receipt_sha256,
        "status": "complete_judge_labels" if counts is not None and counts["uncertain"] == 0 else "unknown",
        "verdict_counts": counts, "requirement_count": len(prepared["inputs"]["requirements"]),
        "judge_precommitted_before_execution": receipt["judge_precommitted_before_execution"],
        "local_file_consistency_checked": True, "semantic_accuracy_verified": False,
        "official_evaluation": False, "promotion_eligible": False}
