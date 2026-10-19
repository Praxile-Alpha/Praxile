"""Bounded task-local hypotheses, never an approved knowledge asset."""
from copy import deepcopy

from .experience_conditions import check_conditions
from .utils import new_id


def add_note(trajectory: dict, action: dict, observations: dict) -> str:
    text = action.get("note_text")
    ttl = action.get("ttl_steps")
    requirements = action.get("requirements")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("note_text must contain 1..2000 characters")
    if type(ttl) is not int or not 1 <= ttl <= 20:
        raise ValueError("ttl_steps must be an integer in 1..20")
    if requirements is None or check_conditions(requirements, observations)["status"] != "matched":
        raise ValueError("explicit note conditions must match current observations")
    episode = next((item for item in trajectory.get("subtask_episodes", [])
        if item.get("episode_id") == action.get("source_episode_id")
        and item.get("task_id") == trajectory["task_id"] and item.get("status") == "closed"), None)
    if episode is None:
        raise ValueError("note requires a closed episode from this task")
    evidence = [item["action_id"] for item in trajectory.get("actions", [])
        if item.get("action_id") in episode.get("action_refs", [])
        and item.get("action_type") not in {"subtask_note", "subtask_begin", "subtask_end", "finish"}]
    if not evidence:
        raise ValueError("source episode has no observed action evidence")
    notes = trajectory.setdefault("task_notes", [])
    if len(notes) >= 16:
        raise ValueError("task note ledger limit reached (16)")
    identity = new_id("note")
    notes.append({"schema_version": "praxile.task_note.v1", "note_id": identity,
        "task_id": trajectory["task_id"], "text": text.strip(), "source_episode_id": episode["episode_id"],
        "action_refs": evidence, "requirements": deepcopy(requirements),
        "expires_at_action_count": len(trajectory.get("actions", [])) + 1 + ttl,
        "status": "active", "independently_verified": False})
    return identity


def select_notes(trajectory: dict, observations: dict) -> list[dict]:
    selected = []
    for note in trajectory.get("task_notes", []):
        if note["status"] != "active":
            continue
        if note["task_id"] != trajectory["task_id"] or len(trajectory["actions"]) >= note["expires_at_action_count"]:
            note["status"] = "expired"
        elif check_conditions(note["requirements"], observations)["status"] != "matched":
            note["status"] = "invalidated"
        else:
            selected.append({key: note[key] for key in ("note_id", "text", "source_episode_id", "action_refs")})
    return selected
