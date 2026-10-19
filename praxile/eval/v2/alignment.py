"""Conservative operation identity alignment; never positional or causal."""
from collections import defaultdict
from collections.abc import Mapping
import hashlib
import json


def _identity(event):
    if event.type != "TOOL_CALL":
        return None
    payload = event.payload
    if isinstance(payload.get("action"), Mapping):
        action = dict(payload["action"])
        kind = action.pop("type", "run_command" if "command" in action else None)
        action.pop("id", None)
        action.pop("tool_call_id", None)
    elif isinstance(payload.get("input"), Mapping):
        kind = payload.get("tool")
        action = dict(payload["input"])
        action.pop("type", None)
    else:
        return None
    required = {"read_file": "path", "search": "pattern", "run_command": "command"}
    if kind not in required or not isinstance(action.get(required[kind]), str) or not action[required[kind]]:
        return None
    try:
        encoded = json.dumps([kind, action], sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        return None
    if "[truncated" in encoded or len(encoded) > 100_000:
        return None
    return kind, hashlib.sha256(encoded.encode()).hexdigest()


def align_operations(baseline_events, candidate_events):
    def group(events):
        groups = defaultdict(list)
        unsupported = []
        unsupported_kinds = {}
        for event in events:
            if event.type != "TOOL_CALL":
                continue
            identity = _identity(event)
            if identity is None:
                unsupported.append(event.event_id)
                action = event.payload.get("action")
                kind = (action.get("type", "run_command" if "command" in action else None)
                        if isinstance(action, Mapping) else event.payload.get("tool"))
                unsupported_kinds[event.event_id] = kind if isinstance(kind, str) else None
            else:
                groups[identity].append(event.event_id)
        return groups, unsupported, unsupported_kinds

    left, left_unknown, left_kinds = group(baseline_events)
    right, right_unknown, right_kinds = group(candidate_events)
    pairs, unpaired = [], []
    for kind, digest in sorted(set(left) | set(right)):
        a, b = left.get((kind, digest), []), right.get((kind, digest), [])
        if len(a) == len(b) == 1:
            pairs.append({"operation": kind, "identity_digest": digest,
                "baseline_event_id": a[0], "candidate_event_id": b[0], "state_equivalence": "unknown"})
        else:
            unpaired.append({"operation": kind, "identity_digest": digest,
                "baseline_event_ids": a, "candidate_event_ids": b,
                "reason": "ambiguous repetition" if len(a) > 1 or len(b) > 1 else "no counterpart"})
    return {"schema_version": "praxile.operation_alignment.v1", "strategy": "unique_exact_operation",
            "pairs": pairs, "unpaired": unpaired,
            "unsupported": {"baseline": left_unknown, "candidate": right_unknown},
            "unsupported_kinds": {"baseline": left_kinds, "candidate": right_kinds},
            "causal_alignment": False,
            "limitations": ["Same operation does not establish equivalent working directory, environment or state.",
                            "Duplicate operations are not aligned by their ordinal position."]}
