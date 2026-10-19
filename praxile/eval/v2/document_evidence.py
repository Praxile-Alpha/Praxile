"""Whitelist rendered layout facts without document text or evaluator material."""
import hashlib
import re

from .schema import canonical_json


def _action(call, kind):
    action = call.payload.get("action")
    if not isinstance(action, dict) and call.payload.get("tool") == kind:
        value = call.payload.get("input")
        action = {**value, "type": kind} if isinstance(value, dict) else None
    return action if isinstance(action, dict) and action.get("type") == kind else None


def _data(result):
    observation = result.payload.get("observation")
    data = observation.get("data") if isinstance(observation, dict) else None
    return data if isinstance(data, dict) else {}


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def project_exit_code(data):
    """Normalize supported executors without inventing or resolving conflicting codes."""
    if not isinstance(data, dict):
        return None
    values = [data[name] for name in ("exit_code", "returncode") if name in data]
    if not values or any(type(value) is not int for value in values) or len(set(values)) != 1:
        return None
    return values[0]


def project_document_read(call, result):
    action, data = _action(call, "read_document"), _data(result)
    if (action is None or result.payload.get("status") != "success"
            or not isinstance(action.get("path"), str) or not action["path"] or "[truncated" in action["path"]
            or data.get("path") != action["path"]
            or not _sha(data.get("source_sha256")) or data.get("unit_kind") not in {"row", "page", "block"}):
        return None
    total, units, start = data.get("total_units"), data.get("units"), action.get("start", 1)
    if (type(total) is not int or not 0 <= total <= 100_000_000 or not isinstance(units, list)
            or len(units) > 1000 or type(start) is not int or not 1 <= start <= 100000):
        return None
    for key, minimum, maximum in (("limit", 1, 20), ("char_offset", 0, 10000000)):
        if key in action and (type(action[key]) is not int or not minimum <= action[key] <= maximum):
            return None
    facts = {"source_sha256": data["source_sha256"], "unit_kind": data["unit_kind"],
        "total_units": total, "returned_units": len(units), "start_unit": start,
        "requested_limit": action.get("limit"), "requested_char_offset": action.get("char_offset"),
        "semantic_quality_verified": False, "official_evaluation": False}
    for key in ("columns", "next_start"):
        value = data.get(key)
        if type(value) is int and 0 <= value <= 100_000_000:
            facts[key] = value
    if type(data.get("columns_truncated")) is bool:
        facts["columns_truncated"] = data["columns_truncated"]
    # Hash the selection, never export file/sheet names or extracted text.
    selected = {key: action[key] for key in ("path", "start", "limit", "sheet", "char_offset") if key in action}
    digest = hashlib.sha256(canonical_json(selected).encode()).hexdigest()
    return ("read_document", digest), facts


def document_read_links(events):
    """Compare observed versions at one path, never infer a causal write or quality."""
    calls, previous, links = {}, {}, {}
    for event in events:
        scope = (event.task_id, event.trace_id)
        call_key = (*scope, event.tool_call_id)
        if event.type == "TOOL_CALL" and event.tool_call_id:
            calls[call_key] = event
        if event.type != "TOOL_RESULT" or call_key not in calls:
            continue
        call = calls[call_key]
        if project_document_read(call, event) is None:
            continue
        data = _data(event)
        key = (*scope, data["path"])
        prior = previous.get(key)
        links[event.event_id] = {
            "previous_read_event_id": prior[0] if prior else None,
            "previous_observed_source_sha256": prior[1] if prior else None,
            "observed_version_relation": ("same_bytes" if prior[1] == data["source_sha256"] else "different_bytes") if prior else "unknown",
            "comparison_basis": "same_task_trace_and_recorded_path",
            "causal_effect_verified": False,
        }
        previous[key] = (event.event_id, data["source_sha256"])
    return links


def project_deliverable_check(call, result):
    action, data = _action(call, "check_deliverables"), _data(result)
    if (action is None or data.get("schema_version") != "praxile.deliverable_check.v1"
            or data.get("official_evaluation") is not False or data.get("semantic_quality_verified") is not False
            or not _sha(data.get("contract_sha256")) or type(data.get("passed")) is not bool
            or data.get("contract_binding") not in {"run_argument", "model_proposed"}):
        return None
    outputs = data.get("outputs")
    allowed = {"passed", "failed", "unsupported", "optional_missing"}
    if (not isinstance(outputs, list) or len(outputs) > 1000
            or any(not isinstance(item, dict) or item.get("status") not in allowed for item in outputs)):
        return None
    facts = {"contract_sha256": data["contract_sha256"], "contract_binding": data["contract_binding"],
        "public_check_passed": data["passed"], "output_count": len(outputs),
        "output_status_counts": {status: sum(item["status"] == status for item in outputs) for status in sorted(allowed)},
        "semantic_quality_verified": False, "official_evaluation": False,
        "user_acceptance_verified": False}
    return ("check_deliverables", data["contract_sha256"]), facts


def project_revision(call, result):
    """Expose version transitions, not replacement text or quality assertions."""
    action, data = _action(call, "revise_docx"), _data(result)
    if action is None or result.payload.get("status") != "success":
        return None
    path, before, after = action.get("path"), action.get("expected_sha256"), data.get("sha256")
    if (not isinstance(path, str) or not path or "[truncated" in path
            or data.get("path") != path or not _sha(before) or not _sha(after)
            or data.get("before_sha256") != before or data.get("changed") is not True):
        return None
    document = action.get("document")
    if not isinstance(document, dict):
        return None
    try:
        digest = hashlib.sha256(canonical_json(action).encode()).hexdigest()
    except (TypeError, ValueError):
        return None
    return ("revise_docx", digest), {
        "before_sha256": before, "after_sha256": after,
        "bytes_changed": before != after,
        "visual_quality_verified": False, "semantic_quality_verified": False,
        "official_evaluation": False,
    }


def revision_layout_links(events):
    """Link a checked byte version only when one earlier revision produced it."""
    calls, revisions, layouts, links = {}, {}, {}, {}
    for event in events:
        if event.type == "TOOL_CALL" and event.tool_call_id:
            calls[event.tool_call_id] = event
        if event.type != "TOOL_RESULT" or event.tool_call_id not in calls:
            continue
        call = calls[event.tool_call_id]
        revision = project_revision(call, event)
        if revision is not None:
            data = _data(event)
            prior_layouts = layouts.get((data["path"], data["before_sha256"]), [])
            revisions.setdefault((data["path"], data["sha256"]), []).append(
                (event.event_id, list(prior_layouts)))
        if project_layout(call, event) is None:
            continue
        data = _data(event)
        digest = data.get("source_sha256")
        matches = revisions.get((data.get("path"), digest), []) if _sha(digest) else []
        links[event.event_id] = {
            "revision_event_id": matches[0][0] if len(matches) == 1 else None,
            "revision_link_status": "unique_content_digest" if len(matches) == 1 else "unknown",
            "before_layout_event_id": None,
            "page_delta": None,
            "page_comparison_status": "unknown",
            "causal_effect_verified": False,
        }
        renderer = data.get("renderer_executable")
        if len(matches) == 1 and isinstance(renderer, str) and renderer:
            comparable = [(event_id, before) for event_id, before in matches[0][1]
                          if before.get("max_pages") == data.get("max_pages")
                          and before.get("renderer_executable") == renderer]
            if len(comparable) == 1:
                before_id, before = comparable[0]
                links[event.event_id].update(
                    before_layout_event_id=before_id,
                    page_delta=data["pages"] - before["pages"],
                    page_comparison_status="matched_recorded_versions_and_renderer",
                )
        if _sha(digest):
            layouts.setdefault((data["path"], digest), []).append((event.event_id, data))
    return links


def project_layout(call, result):
    action = call.payload.get("action")
    if not isinstance(action, dict):
        action = call.payload.get("input")
        if not isinstance(action, dict) or call.payload.get("tool") != "check_document_layout":
            return None
        action = {**action, "type": "check_document_layout"}
    if action.get("type") != "check_document_layout":
        return None
    path, maximum = action.get("path"), action.get("max_pages")
    if not isinstance(path, str) or not path or "[truncated" in path:
        return None
    if maximum is not None and (type(maximum) is not int or not 1 <= maximum <= 2000):
        return None
    observation = result.payload.get("observation")
    data = observation.get("data") if isinstance(observation, dict) else None
    if not isinstance(data, dict) or data.get("schema_version") != "praxile.document_layout.v1":
        return None
    pages = data.get("pages")
    if (data.get("rendered") is not True or type(pages) is not int or not 1 <= pages <= 2000
            or data.get("path") != path or data.get("max_pages") != maximum
            or type(data.get("max_pages")) is not type(maximum)
            or data.get("constraint_source") != "action_argument"):
        return None
    met = None if maximum is None else pages <= maximum
    status = "failure" if met is False else "success"
    if data.get("page_limit_met") is not met or result.payload.get("status") != status:
        return None
    digest = hashlib.sha256(canonical_json({"path": path, "max_pages": maximum}).encode()).hexdigest()
    return ("check_document_layout", digest), {
        "pages": pages, "max_pages": maximum, "page_limit_met": met,
        "rendered": True, "constraint_source": "action_argument",
        "visual_quality_verified": False, "semantic_quality_verified": False,
        "official_evaluation": False,
    }
