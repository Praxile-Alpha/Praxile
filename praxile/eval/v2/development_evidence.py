"""Minimal trace projection with development membership and manifest checks."""
from collections import Counter
import hashlib
import re

from ...utils import read_json
from .alignment import _identity
from .document_evidence import project_layout, project_revision, revision_layout_links
from .manifest import ImmutableManifestStore
from .schema import EvalSchemaError, canonical_json


def collect_development_evidence(state_root, event_store, *, experiment_id, arm,
                                 development_task_ids, heldout_task_ids, limit=20):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", experiment_id):
        raise EvalSchemaError("unsafe source experiment ID")
    if arm not in {"baseline", "candidate"} or type(limit) is not int or not 1 <= limit <= 20:
        raise EvalSchemaError("invalid source arm or evidence limit")
    dev, heldout = set(development_task_ids), set(heldout_task_ids)
    if not dev or dev & heldout:
        raise EvalSchemaError("invalid development/held-out membership")
    root = state_root.resolve() / "eval/v2/experiments" / experiment_id
    plan = read_json(root / "manifest.json", {})
    if (plan.get("schema_version") != "praxile.ab_experiment.v1" or plan.get("track") != "development"
            or plan.get("experiment_id") != experiment_id or plan.get("capability_protocol") is not None):
        raise EvalSchemaError("source must be an explicit development experiment, not a held-out run")
    run_id = plan.get(arm + "_run_id")
    if not isinstance(run_id, str):
        raise EvalSchemaError("missing source run")
    manifests = ImmutableManifestStore(state_root)
    manifest = manifests.load(run_id)
    task_set = manifest.reproducibility["task_set"]
    task_ids = {item["task_id"] for item in task_set["tasks"]}
    if not task_ids or not task_ids <= dev or task_ids & heldout or task_set["digest"] != plan.get("task_set_digest"):
        raise EvalSchemaError("source task membership or digest mismatch")
    report = read_json(manifests.path_for(run_id).parent / "report.json", {})
    if report.get("eval_run_id") != run_id or report.get("manifest_digest") != manifest.reproducibility_digest:
        raise EvalSchemaError("source report does not match frozen manifest")
    rows = report.get("tasks", [])
    if len(rows) != len(task_ids) or {row.get("task_id") for row in rows} != task_ids:
        raise EvalSchemaError("source report task membership mismatch")
    evidence, sources, priorities, seen = [], [], [], set()
    for row in sorted(rows, key=lambda item: item["task_id"]):
        trace_id = row.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            continue
        agent_run_id = row.get("agent_run_id")
        if (not isinstance(agent_run_id, str) or not agent_run_id
                or row.get("eval_run_id") != run_id
                or row.get("manifest_digest") != manifest.reproducibility_digest):
            raise EvalSchemaError("source task run binding mismatch")
        events = event_store.list_events(trace_id=trace_id)
        if any(event.trace_id != trace_id or event.task_id != row["task_id"]
               or event.run_id != agent_run_id for event in events):
            raise EvalSchemaError("trace events do not belong to the source task")
        calls, counts = {}, Counter()
        layout_links = revision_layout_links(events)
        for event in events:
            if event.event_id in seen:
                raise EvalSchemaError("duplicate source event ID")
            seen.add(event.event_id)
            if event.type == "TOOL_CALL" and event.tool_call_id:
                if event.tool_call_id in calls:
                    raise EvalSchemaError("ambiguous tool call ID")
                calls[event.tool_call_id] = event
            if event.type != "TOOL_RESULT" or event.tool_call_id not in calls:
                continue
            call = calls[event.tool_call_id]
            identity = _identity(call)
            layout = project_layout(call, event)
            document = layout or project_revision(call, event)
            if document is not None:
                identity = document[0]
            if identity is None:
                continue
            status = event.payload.get("status")
            if status not in {"success", "failure", "error", "blocked", "timeout", "timed_out"}:
                continue
            counts[identity] += 1
            observation = event.payload.get("observation")
            data = observation.get("data") if isinstance(observation, dict) else None
            exit_code = data.get("exit_code") if isinstance(data, dict) else None
            projected = {"operation": identity[0], "parameter_sha256": identity[1],
                         "status": status, "same_parameter_occurrence": counts[identity],
                         "exit_code": exit_code if type(exit_code) is int else None,
                         "call_event_id": call.event_id, "state_equivalence": "unknown"}
            if document is not None:
                projected.update(document[1])
            if event.event_id in layout_links:
                projected.update(layout_links[event.event_id])
            evidence.append({"task_id": row["task_id"], "event_id": event.event_id,
                             "observation": canonical_json(projected)})
            priorities.append(1 if status == "success" else 0)
            sources.append({"event_id": event.event_id, "trace_id": trace_id,
                "event_sha256": hashlib.sha256(event.to_json().encode()).hexdigest()})
    # Prioritize failures; preserve deterministic order within each group.
    order = sorted(range(len(evidence)), key=lambda i: (priorities[i], i))[:limit]
    return {"schema_version": "praxile.development_evidence.v1", "experiment_id": experiment_id,
            "arm": arm, "manifest_digest": manifest.reproducibility_digest,
            "evidence": [evidence[i] for i in order], "sources": [sources[i] for i in order],
            "eligible_count": len(evidence), "omitted_count": max(0, len(evidence) - limit),
            "projection": "structured_tool_outcomes_no_raw_text",
            "limitations": ["Local manifest membership is not an external provenance attestation.",
                            "No raw outputs, evaluator scores or task correctness claims are projected."]}


def design_from_development_experiment(state_root, event_store, *, experiment_id, arm,
                                      development_task_ids, heldout_task_ids, **design_options):
    from .candidate_designer import design_development_candidates
    bundle = collect_development_evidence(state_root, event_store, experiment_id=experiment_id,
        arm=arm, development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids)
    if not bundle["evidence"]:
        return {"decision": "NO_ENTRY", "candidates": [], "composition": None,
                "promotion_eligible": False, "source_evidence": bundle}
    result = design_development_candidates(evidence=bundle["evidence"],
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids, **design_options)
    result["source_evidence"] = bundle
    return result
