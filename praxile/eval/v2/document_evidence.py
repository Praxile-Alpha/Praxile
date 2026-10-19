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
    calls, revisions, links = {}, {}, {}
    for event in events:
        if event.type == "TOOL_CALL" and event.tool_call_id:
            calls[event.tool_call_id] = event
        if event.type != "TOOL_RESULT" or event.tool_call_id not in calls:
            continue
        call = calls[event.tool_call_id]
        revision = project_revision(call, event)
        if revision is not None:
            data = _data(event)
            revisions.setdefault((data["path"], data["sha256"]), []).append(event.event_id)
        if project_layout(call, event) is None:
            continue
        data = _data(event)
        digest = data.get("source_sha256")
        matches = revisions.get((data.get("path"), digest), []) if _sha(digest) else []
        links[event.event_id] = {
            "revision_event_id": matches[0] if len(matches) == 1 else None,
            "revision_link_status": "unique_content_digest" if len(matches) == 1 else "unknown",
            "causal_effect_verified": False,
        }
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
