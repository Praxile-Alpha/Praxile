"""Bind official trial imports to native evidence without copying judge material."""
import json
import re

from .v2.schema import EvalSchemaError
from ..trace import native_run_to_events


def bind_native_trace(trial, read, evidence):
    """Use the importer's checked reader and raw-byte digest ledger.

    Legacy trials remain usable, but never acquire a verified binding merely
    because similarly named agent files happen to exist alongside them.
    """
    def optional_object(parent, key):
        value = parent.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise EvalSchemaError("invalid native trial metadata object")
        return value

    agent = optional_object(trial, "agent_result")
    metadata = optional_object(agent, "metadata")
    native = optional_object(metadata, "praxile")
    digest = native.get("source_result_sha256")
    if digest is None:
        return {"status": "unbound", "reason": "trial lacks native source digest",
                "provenance_attested": False}
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise EvalSchemaError("invalid native source digest in trial")
    result = read("agent/praxile-result.json")
    projection = read("agent/praxile-events.json")
    if evidence["agent/praxile-result.json"] != "sha256:" + digest:
        raise EvalSchemaError("official trial/native result digest mismatch")
    trajectory = result.get("trajectory")
    if (not isinstance(trajectory, dict)
            or any(not isinstance(trajectory.get(key), str) or not trajectory[key].strip()
                   for key in ("task_id", "start_time"))
            or trajectory["task_id"] != native.get("trajectory_task_id")
            or result.get("experience_mode") != native.get("experience_mode")):
        raise EvalSchemaError("official trial/native trajectory identity mismatch")
    events = native_run_to_events(result)
    if (set(projection) != {"schema_version", "source_result_sha256", "events"}
            or projection["schema_version"] != "praxile.native_event_projection.v1"
            or projection["source_result_sha256"] != digest
            or json.dumps(projection["events"], sort_keys=True, allow_nan=False)
            != json.dumps([event.to_dict() for event in events], sort_keys=True, allow_nan=False)):
        raise EvalSchemaError("official trial/native event projection mismatch")
    return {"status": "bound", "source_result_sha256": digest,
            "trace_id": events[0].trace_id, "run_id": events[0].run_id,
            "trajectory_task_id": events[0].task_id, "event_count": len(events),
            "experience_mode": result["experience_mode"], "provenance_attested": False}
