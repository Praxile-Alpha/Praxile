"""Controller-owned online prefix admission, never held-out evaluation."""
import hashlib
from .v2.candidate import ContextCandidate
from .v2.schema import canonical_json


def validate_online_protocol(protocol, task, value, file_digest, repository):
    if (not isinstance(protocol, dict) or set(protocol) != {"completed_task_ids", "task_id", "candidate_file_sha256", "repository"}
            or protocol["task_id"] != task.task_id or task.split != "online_stream"
            or protocol["candidate_file_sha256"] != file_digest or protocol["repository"] != repository):
        raise ValueError("invalid online prefix identity")
    completed = protocol["completed_task_ids"]
    if (not isinstance(completed, list) or not completed
            or any(not isinstance(item, str) or not item for item in completed)
            or len(set(completed)) != len(completed) or task.task_id in completed):
        raise ValueError("online prefix must contain unique prior tasks")
    candidate = ContextCandidate.from_dict(value)
    if not candidate.source_task_ids or not set(candidate.source_task_ids) <= set(completed):
        raise ValueError("online candidate must originate only from the completed prefix")
    return "sha256:" + hashlib.sha256(canonical_json(protocol).encode()).hexdigest()
