"""Read-only, origin-scoped DOM observations configured by the control plane."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit


def origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("probe requires an HTTP origin without credentials")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    return f"{parsed.scheme}://{host}:{port}"


def validate_probes(value: object) -> dict:
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("browser.observation_probes must be a map with at most 16 probes")
    normalized = {}
    for name, probe in value.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
            raise ValueError("invalid browser observation probe name")
        if not isinstance(probe, dict) or set(probe) != {"origin", "selector", "kind"}:
            raise ValueError("browser probe requires origin, selector and kind only")
        if probe["kind"] not in ("count", "visible"):
            raise ValueError("browser probe kind must be count or visible")
        selector = probe["selector"]
        if not isinstance(selector, str) or not selector.strip() or len(selector) > 512:
            raise ValueError("invalid browser probe selector")
        if not isinstance(probe["origin"], str):
            raise ValueError("invalid browser probe origin")
        parsed = urlsplit(probe["origin"])
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("browser probe origin cannot include a path or query")
        normalized[name] = {**probe, "origin": origin(probe["origin"])}
    return normalized


def observe_probes(page, probes: dict) -> dict:
    if not probes:
        return {}
    digest = hashlib.sha256(json.dumps(probes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"dom_profile_sha256": digest, "dom": {}}
    try:
        before = page.url
        current = origin(before)
        for name, probe in probes.items():
            if current != probe["origin"]:
                continue
            try:
                locator = page.locator(probe["selector"])
                count = locator.count()
                if type(count) is not int or count < 0:
                    continue
                if probe["kind"] == "count":
                    result["dom"][name] = count
                elif count == 1:
                    visible = locator.is_visible()
                    if type(visible) is bool:
                        result["dom"][name] = visible
            except Exception:
                # Missing, ambiguous and failed observations are not false.
                continue
        if page.url != before or page.is_closed():
            result["dom"] = {}
    except Exception:
        result["dom"] = {}
    return result
