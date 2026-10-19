"""Import locally supplied native execution evidence, never evaluator material."""
import hashlib
import json
from pathlib import Path

from ..trace import EventStore, native_run_to_events
from ..utils import file_lock, write_json


def _read(path):
    path = Path(path).absolute()
    if path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("trace input must be a regular file without symlink components")
    with path.open("rb") as handle:
        raw = handle.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("trace input exceeds 64 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate trace JSON field")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError("nonfinite trace JSON number")
    return raw, json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


class NativeTraceImportService:
    def __init__(self, paths):
        self.store = EventStore(paths)

    def import_files(self, result_path, projection_path):
        raw, result = _read(result_path)
        _, projection = _read(projection_path)
        digest = hashlib.sha256(raw).hexdigest()
        if (not isinstance(projection, dict)
                or set(projection) != {"schema_version", "source_result_sha256", "events"}
                or projection["schema_version"] != "praxile.native_event_projection.v1"
                or projection["source_result_sha256"] != digest):
            raise ValueError("native event projection source mismatch")
        trajectory = result.get("trajectory") if isinstance(result, dict) else None
        if (not isinstance(trajectory, dict)
                or any(not isinstance(trajectory.get(key), str) or not trajectory[key].strip()
                       for key in ("task_id", "start_time"))):
            raise ValueError("native trace requires stable task identity and start time")
        events = native_run_to_events(result)
        expected = [event.to_dict() for event in events]
        if json.dumps(projection["events"], sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            raise ValueError("native event projection differs from source normalization")
        receipt = {"schema_version": "praxile.native_trace_import.v1", "source_result_sha256": digest,
            "trace_id": events[0].trace_id, "run_id": events[0].run_id, "task_id": events[0].task_id,
            "event_count": len(events), "experience_mode": result["experience_mode"],
            "official_evaluation": "not_imported", "provenance_attested": False}
        receipt_path = self.store.root / "imports" / (digest + ".json")
        with file_lock(receipt_path.with_suffix(".import-operation")):
            appended = self.store.append_many(events)
            write_json(receipt_path, receipt)
        return {**receipt, "appended_events": len(appended), "already_present_events": len(events) - len(appended)}
