"""Independent paired content review of audited composition reruns."""
from copy import deepcopy
from pathlib import Path

from .contracts import BenchmarkTask
from .five_arm_judge import judge_identity
from .online_composition_audit import audit_online_composition
from .online_review import _run_prepared_review
from .online_stream import OnlineStream, _persist
from .owned_content_review import _reader, _json, _sha, prepare_owned_review


def prepare_composition_reviews(stream_directory, index, *, probe_directory, composition_directory,
                                manifest_sha256, report_sha256, judge, directory):
    audit = audit_online_composition(stream_directory, probe_directory=probe_directory,
        directory=composition_directory, manifest_sha256=manifest_sha256, report_sha256=report_sha256)
    if audit["status"] != "composition_rerun_not_scored":
        raise ValueError("composition has no independent rerun to review")
    stream = OnlineStream(stream_directory)
    target = stream.review_target(index)
    task = BenchmarkTask.from_dict({**target["task"].to_dict(), "split": "development"})
    judge = deepcopy(judge)
    identity = judge_identity(judge, {task.task_id: task})
    frozen = target["frozen_judge_identity"]
    if frozen is not None and identity != {**frozen, "artifacts": {task.task_id: frozen["artifacts"][task.task_id]}}:
        raise ValueError("composition judge differs from precommitted stream protocol")
    root = Path(directory).absolute()
    protected = [Path(path).resolve() for path in (stream_directory, probe_directory, composition_directory)]
    evaluator = Path(judge["evaluator_root"]).resolve()
    if root.exists() or root.resolve() != root or any(root.is_relative_to(path) for path in protected):
        raise ValueError("paired review directory must be new and outside learning state")
    if any(location.is_relative_to(path) for path in [*protected, root]
           for location in (evaluator, (evaluator / task.evaluator.private_ref).resolve())):
        raise ValueError("evaluator must be isolated from execution and learning state")
    composition_root = Path(composition_directory).absolute()
    read = _reader(composition_root)
    raw = read("report.json")
    if _sha(raw) != report_sha256:
        raise ValueError("composition changed during review preparation")
    completion = _json(raw)
    rerun_raw = read("rerun/report.json", max_bytes=16 * 1024**2)
    if _sha(rerun_raw) != completion["rerun_report_sha256"]:
        raise ValueError("composition rerun changed during preparation")
    rows = [row for row in _json(rerun_raw)["attempts"] if row["task_index"] == index]
    if [row["arm"] for row in rows] != ["baseline", "composition"]:
        raise ValueError("task lacks a complete composition rerun pair")
    prepared = []
    for row in rows:
        inputs, binding = prepare_owned_review(task,
            execution_dir=(composition_root / "rerun" / row["report"]).parent,
            evaluator_root=evaluator, **judge["artifacts"][task.task_id])
        if binding["execution_sha256"] != row["report_sha256"]:
            raise ValueError("review artifacts differ from audited composition execution")
        receipt = {"schema_version": "praxile.online_composition_content_review.v1", "track": "online_prefix_composition",
            "arm": row["arm"], "task_id": task.task_id, "task_index": index, "task_fingerprint": task.fingerprint,
            "composition_manifest_sha256": manifest_sha256, "composition_report_sha256": report_sha256,
            "source_ledger_sha256": audit["source_ledger_sha256"], "execution_binding": binding,
            "judge_identity": identity, "judge_precommitted_before_execution": frozen is not None,
            "shared_stream_budget": target["shared_stream_budget"], "official_evaluation": False,
            "promotion_eligible": False, "learning_feedback_allowed": False}
        prepared.append({"root": root / row["arm"], "receipt": receipt,
                         "inputs": inputs, "binding": binding, "judge": judge})
    return prepared


def run_composition_reviews(stream_directory, index, **options):
    prepared = prepare_composition_reviews(stream_directory, index, **options)
    root = Path(options["directory"]).absolute()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {"schema_version": "praxile.online_composition_review_pair.v1", "track": "online_prefix_composition",
        "task_index": index, "status": "outcome_unknown", "reviews": [], "automatic_replay_allowed": False,
        "decision": "KEEP_BASE", "promotion_eligible": False, "learning_feedback_allowed": False,
        "official_evaluation": False, "semantic_accuracy_verified": False}
    _persist(root / "report.json", report)
    try:
        for item in prepared:
            path = _run_prepared_review(item, quota_factory=lambda: OnlineStream(stream_directory).shared_budget())
            raw = path.read_bytes()
            receipt = _json(raw)
            report["reviews"].append({"arm": item["receipt"]["arm"], "receipt": path.relative_to(root).as_posix(),
                "receipt_sha256": _sha(raw), "status": receipt["status"]})
            _persist(root / "report.json", report)
        report["status"] = "reviews_returned_not_promoted"
    except BaseException as error:
        report["error_type"] = type(error).__name__
        raise
    finally:
        _persist(root / "report.json", report)
    return root / "report.json"
