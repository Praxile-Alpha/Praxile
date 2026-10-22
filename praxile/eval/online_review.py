"""Post-hoc independent content review, isolated from online learning state."""
from copy import deepcopy
from pathlib import Path

from ..utils import write_json
from .broker_content_review import run_broker_content_review
from .five_arm_judge import judge_identity
from .online_stream import OnlineStream
from .owned_content_review import _reader, _json, _sha, prepare_owned_review
from .persistent_chat_budget import PersistentChatBudget


def prepare_online_review(stream_directory, index, *, judge, directory):
    target = OnlineStream(stream_directory).review_target(index)
    task = target["task"]
    judge = deepcopy(judge)
    identity = judge_identity(judge, {task.task_id: task})
    stream = Path(stream_directory).resolve()
    root = Path(directory).absolute()
    evaluator = Path(judge["evaluator_root"]).resolve()
    if root.resolve() != root or root.exists():
        raise ValueError("review directory must be new and free of symlinks")
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
        "judge_precommitted_before_execution": False, "learning_feedback_allowed": False}
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
