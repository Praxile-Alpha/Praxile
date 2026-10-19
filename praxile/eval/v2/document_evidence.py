"""Whitelist rendered layout facts without document text or evaluator material."""
import hashlib

from .schema import canonical_json


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
