"""Explicit development-only projection of native execution evidence."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from ...services.native_trace_import import _read
from ...trace import native_run_to_events
from .alignment import _identity
from .development_evidence import select_evidence
from .document_evidence import project_layout, project_revision, revision_layout_links
from .schema import EvalSchemaError, canonical_json


def collect_native_evidence(result_path, *, expected_sha256, development_task_ids,
                            heldout_task_ids, track, limit=20):
    """Membership is caller-declared, not attested; never read evaluator files."""
    for members in (development_task_ids, heldout_task_ids):
        if (not isinstance(members, (list, tuple, set, frozenset))
                or any(not isinstance(item, str) or not item.strip() for item in members)):
            raise EvalSchemaError("task membership must be an explicit collection of IDs")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise EvalSchemaError("explicit native source SHA256 required")
    dev, heldout = set(development_task_ids), set(heldout_task_ids)
    if track != "development" or not dev or dev & heldout:
        raise EvalSchemaError("explicit disjoint development membership required")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise EvalSchemaError("evidence limit must be in [1, 20]")
    raw, result = _read(result_path)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise EvalSchemaError("native evidence digest mismatch")
    trajectory = result.get("trajectory") if isinstance(result, dict) else None
    task = trajectory.get("task_id") if isinstance(trajectory, dict) else None
    if not isinstance(task, str) or task not in dev or task in heldout:
        raise EvalSchemaError("native source is not a declared development task")
    if not isinstance(trajectory.get("start_time"), str) or not trajectory["start_time"].strip():
        raise EvalSchemaError("stable native start time required")
    events = native_run_to_events(result)
    calls, counts, seen = {}, Counter(), set()
    evidence, sources, priorities = [], [], []
    links = revision_layout_links(events)
    for event in events:
        if event.event_id in seen or event.task_id != task:
            raise EvalSchemaError("ambiguous native event identity")
        seen.add(event.event_id)
        if event.type == "TOOL_CALL" and event.tool_call_id:
            if event.tool_call_id in calls:
                raise EvalSchemaError("ambiguous native tool call")
            calls[event.tool_call_id] = event
        if event.type != "TOOL_RESULT" or event.tool_call_id not in calls:
            continue
        call = calls[event.tool_call_id]
        document = project_layout(call, event) or project_revision(call, event)
        identity = document[0] if document else _identity(call)
        status = event.payload.get("status")
        if identity is None or status not in {"success", "failure", "error", "blocked", "timeout", "timed_out"}:
            continue
        counts[identity] += 1
        observation = event.payload.get("observation")
        data = observation.get("data") if isinstance(observation, dict) else None
        exit_code = data.get("exit_code") if isinstance(data, dict) else None
        facts = {"operation": identity[0], "parameter_sha256": identity[1], "status": status,
            "same_parameter_occurrence": counts[identity], "call_event_id": call.event_id,
            "exit_code": exit_code if type(exit_code) is int else None, "state_equivalence": "unknown"}
        if document:
            facts.update(document[1])
        facts.update(links.get(event.event_id, {}))
        evidence.append({"task_id": task, "event_id": event.event_id, "observation": canonical_json(facts)})
        sources.append({"event_id": event.event_id, "trace_id": event.trace_id,
                        "event_sha256": hashlib.sha256(event.to_json().encode()).hexdigest()})
        priorities.append(1 if status == "success" else 0)
    order = select_evidence(evidence, priorities, limit)
    return {"schema_version": "praxile.native_development_evidence.v1", "track": track,
        "source_result_sha256": digest, "task_id": task,
        "evidence": [evidence[i] for i in order], "sources": [sources[i] for i in order],
        "eligible_count": len(evidence), "omitted_count": len(evidence) - len(order),
        "projection": "structured_tool_outcomes_no_raw_text", "provenance_attested": False,
        "promotion_eligible": False,
        "limitations": ["Development membership is explicitly supplied by the caller, not independently attested.",
            "No task correctness, evaluator score, raw command/output or causal claim is projected."]}


def design_from_native(result_path, *, expected_sha256, development_task_ids, heldout_task_ids,
                       track, limit=20, **design_options):
    from .candidate_designer import design_development_candidates
    bundle = collect_native_evidence(result_path, expected_sha256=expected_sha256,
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids, track=track, limit=limit)
    result = (design_development_candidates(evidence=bundle["evidence"],
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids, **design_options)
        if bundle["evidence"] else {"decision": "NO_ENTRY", "candidates": [], "composition": None,
                                   "promotion_eligible": False})
    return {**result, "source_evidence": bundle}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--development-task-id", action="append", required=True)
    parser.add_argument("--heldout-task-id", action="append", default=[])
    parser.add_argument("--track", choices=["development"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = collect_native_evidence(args.result, expected_sha256=args.sha256,
        development_task_ids=args.development_task_id, heldout_task_ids=args.heldout_task_id, track=args.track)
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(bundle, handle, indent=2)
    print(f"Projected {len(bundle['evidence'])} observations; no model request or promotion.")


if __name__ == "__main__":
    main()
