"""Generate unapproved candidates from bound, completed online observations."""
from .owned_content_review import _reader, _json
from .online_stream import OnlineStream
from .v2.native_development_evidence import collect_native_evidence


def collect_online_evidence(directory, *, limit=20):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("online evidence limit must be in [1, 20]")
    snapshot = OnlineStream(directory).completed_sources()
    evidence, sources, seen = [], [], set()
    # Prefer recent completed tasks, while keeping source identity and omissions explicit.
    for source in reversed(snapshot["sources"]):
        path = source["path"]
        result = _json(_reader(path.parent)(path.name, max_bytes=16 * 1024**2))
        trajectory = result.get("trajectory", {})
        native_id = trajectory.get("task_id") if isinstance(trajectory, dict) else None
        if not isinstance(native_id, str) or not native_id:
            raise ValueError("online source lacks a native task identity")
        bundle = collect_native_evidence(path, expected_sha256=source["sha256"],
            development_task_ids=[native_id], heldout_task_ids=[], track="online_stream", limit=limit)
        for item in bundle["evidence"]:
            if item["event_id"] in seen:
                raise ValueError("ambiguous event identity across online tasks")
            seen.add(item["event_id"])
        selected = bundle["evidence"][:limit - len(evidence)]
        evidence.extend({**item, "task_id": source["task_id"]} for item in selected)
        sources.append({"task_id": source["task_id"], "native_task_id": native_id,
            "source_result_sha256": source["sha256"], "event_sources": bundle["sources"][:len(selected)],
            "eligible_count": bundle["eligible_count"], "selected_count": len(selected)})
        if len(evidence) == limit:
            break
    return {"schema_version": "praxile.online_design_evidence.v1", "track": "online_stream",
        "ledger_sha256": snapshot["ledger_sha256"], "completed_task_ids": snapshot["completed_task_ids"],
        "evidence": evidence, "sources": sources,
        "omitted_completed_tasks": len(snapshot["sources"]) - len(sources),
        "selection": "recent_completed_tasks_then_bounded_priority_selection",
        "projection": "structured_tool_outcomes_no_raw_text", "promotion_eligible": False}


def run_online_design(config, directory, *, design_id, applies_to, resume=False,
                      max_output_tokens=4000, request_timeout_seconds=60, executor_profile=None):
    from .native_run_claim import validate_run_id
    validate_run_id(design_id)
    from .v2.candidate_design_run import _run_design, _validate_timeout
    _validate_timeout(request_timeout_seconds)
    if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 16384:
        raise ValueError("invalid candidate output budget")
    bundle = collect_online_evidence(directory)
    quota = OnlineStream(directory).shared_budget()
    return _run_design(config, bundle, design_id=design_id,
        development_task_ids=bundle["completed_task_ids"], heldout_task_ids=[], applies_to=applies_to,
        private=False, resume=resume, executor_profile=executor_profile, max_output_tokens=max_output_tokens,
        request_timeout_seconds=request_timeout_seconds, response_mode="text", repair_attempts=0,
        **({"shared_chat_budget": quota} if quota else {}))
