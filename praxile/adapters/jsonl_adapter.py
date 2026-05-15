from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..interop import EXTERNAL_COMPAT_TRAJECTORY_FORMAT, PRAXILE_TRAJECTORY_SCHEMA
from ..utils import stable_hash, utc_now
from .base import AgentAdapter


class GenericJSONLAdapter(AgentAdapter):
    """Import a minimal external agent JSONL trace into Praxile.

    Supported rows are intentionally generic:
    - a full Praxile-like trajectory object;
    - event rows such as task/action/observation/result/reward/message;
    - chat rows with role/content fields.
    """

    name = "generic_jsonl"

    def import_file(self, path: Path) -> dict[str, Any]:
        rows = self._read_jsonl(path)
        trajectory = self.to_trajectory(rows)
        trajectory.setdefault("external_adapter", {})
        trajectory["external_adapter"].update(
            {
                "adapter": self.name,
                "source_path": str(path),
                "format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
                "imported_at": utc_now(),
                "row_count": len(rows),
            }
        )
        return {"trajectory": trajectory, "rows": len(rows), "source_path": str(path)}

    def to_trajectory(self, agent_output: Any) -> dict[str, Any]:
        rows = _coerce_rows(agent_output)
        full = _first_full_trajectory(rows)
        if full:
            return _normalize_trajectory(full, rows=rows)

        task_id = _first_string(rows, ["task_id", "trajectory_id", "run_id", "id"])
        user_task = _first_string(rows, ["user_task", "task", "prompt", "instruction"])
        messages: list[dict[str, Any]] = []
        actions: list[dict[str, Any]] = []
        reward_report: dict[str, Any] = {}
        result: dict[str, Any] = {}
        created_at = _first_string(rows, ["created_at", "start_time", "timestamp"]) or utc_now()

        pending_action: dict[str, Any] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            event = str(row.get("event") or row.get("type") or "").lower()
            if row.get("role") and row.get("content") is not None:
                messages.append({"role": str(row.get("role")), "content": str(row.get("content"))})
                if not user_task and row.get("role") == "user":
                    user_task = str(row.get("content"))
            if event in {"task", "start", "run_start"}:
                task_id = task_id or str(row.get("task_id") or row.get("run_id") or "")
                user_task = user_task or str(row.get("task") or row.get("user_task") or row.get("prompt") or "")
                created_at = str(row.get("created_at") or row.get("timestamp") or created_at)
            elif event in {"action", "tool_call", "tool"} or row.get("action_type") or row.get("tool"):
                pending_action = _action_from_row(row, step=len(actions) + 1)
                actions.append(pending_action)
            elif event in {"observation", "tool_result"}:
                observation = _observation_from_row(row)
                if pending_action is not None:
                    pending_action["observation"] = observation
                    pending_action["status"] = observation.get("status", pending_action.get("status", "success"))
                else:
                    actions.append(
                        {
                            "step": len(actions) + 1,
                            "action_type": "external_observation",
                            "status": observation.get("status", "success"),
                            "input": {},
                            "observation": observation,
                        }
                    )
            elif event in {"result", "finish", "run_end"}:
                result = {
                    "status": _normalize_status(row.get("status")),
                    "summary": str(row.get("summary") or row.get("output") or row.get("content") or ""),
                }
            elif event == "reward" or "reward_report" in row:
                reward_report = row.get("reward_report") if isinstance(row.get("reward_report"), dict) else dict(row)

        if not user_task and messages:
            user_task = str(messages[0].get("content") or "")
        user_task = user_task or "Imported external agent trace"
        if not task_id:
            task_id = f"external_{stable_hash(json.dumps(rows, sort_keys=True, ensure_ascii=False), 16)}"
        if not result:
            failed = any(action.get("status") in {"failure", "failed", "blocked"} for action in actions)
            result = {"status": "failed" if failed else "completed", "summary": "Imported external agent trace."}
        if not reward_report:
            reward_report = {
                "overall": 0.5,
                "should_generate_experience": True,
                "experience_generation": {"should_generate_experience": True, "signals": {"external_import": True}},
            }
        return {
            "schema": PRAXILE_TRAJECTORY_SCHEMA,
            "task_id": task_id,
            "user_task": user_task,
            "start_time": created_at,
            "end_time": utc_now(),
            "environment_snapshot": {"external_adapter": self.name},
            "messages": messages,
            "actions": actions,
            "reward_report": reward_report,
            "result": result,
        }

    def from_proposal(self, proposal: dict[str, Any]) -> dict[str, Any]:
        return {
            "proposal_id": proposal.get("proposal_id"),
            "type": proposal.get("type"),
            "title": proposal.get("title"),
            "status": proposal.get("status", "pending"),
            "risk_level": proposal.get("risk_level"),
            "confidence": proposal.get("confidence"),
            "target_files": proposal.get("target_files") or [],
            "source_task_id": proposal.get("source_task_id"),
        }

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row: {exc.msg}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object row")
            rows.append(parsed)
        if not rows:
            raise ValueError(f"{path}: no JSONL rows found")
        return rows


def _coerce_rows(agent_output: Any) -> list[dict[str, Any]]:
    if isinstance(agent_output, dict):
        if isinstance(agent_output.get("events"), list):
            return [item for item in agent_output["events"] if isinstance(item, dict)]
        return [agent_output]
    if isinstance(agent_output, list):
        return [item for item in agent_output if isinstance(item, dict)]
    raise TypeError("agent_output must be a dict or list of dict rows")


def _first_full_trajectory(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if isinstance(row.get("trajectory"), dict):
            return row["trajectory"]
        if row.get("task_id") and isinstance(row.get("actions"), list):
            return row
    return None


def _normalize_trajectory(raw: dict[str, Any], *, rows: list[dict[str, Any]]) -> dict[str, Any]:
    trajectory = dict(raw)
    trajectory.setdefault("schema", PRAXILE_TRAJECTORY_SCHEMA)
    trajectory.setdefault("task_id", f"external_{stable_hash(json.dumps(rows, sort_keys=True, ensure_ascii=False), 16)}")
    trajectory.setdefault("user_task", trajectory.get("task") or "Imported external agent trace")
    trajectory.setdefault("start_time", trajectory.get("created_at") or utc_now())
    trajectory.setdefault("end_time", utc_now())
    trajectory.setdefault("environment_snapshot", {"external_adapter": "generic_jsonl"})
    trajectory.setdefault("actions", [])
    trajectory.setdefault("result", {"status": "completed", "summary": "Imported external trajectory."})
    trajectory.setdefault(
        "reward_report",
        {
            "overall": 0.5,
            "should_generate_experience": True,
            "experience_generation": {"should_generate_experience": True, "signals": {"external_import": True}},
        },
    )
    return trajectory


def _first_string(rows: list[dict[str, Any]], keys: list[str]) -> str:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _action_from_row(row: dict[str, Any], *, step: int) -> dict[str, Any]:
    action_type = str(row.get("action_type") or row.get("tool") or row.get("name") or "external_tool")
    action_input = row.get("input") if isinstance(row.get("input"), dict) else {}
    if not action_input and row.get("command"):
        action_input = {"command": row.get("command")}
    if not action_input and row.get("path"):
        action_input = {"path": row.get("path")}
    observation = row.get("observation") if isinstance(row.get("observation"), dict) else {}
    return {
        "step": int(row.get("step") or step),
        "action_type": action_type,
        "status": _normalize_status(row.get("status")),
        "input": action_input,
        "observation": observation,
    }


def _observation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": _normalize_status(row.get("status")),
        "output": str(row.get("output") or row.get("content") or row.get("message") or ""),
        "data": row.get("data") if isinstance(row.get("data"), dict) else {},
        "risk_level": str(row.get("risk_level") or "low"),
    }


def _normalize_status(value: Any) -> str:
    status = str(value or "success").lower()
    if status in {"ok", "passed", "pass", "complete", "completed", "success"}:
        return "success" if status not in {"complete", "completed"} else "completed"
    if status in {"fail", "failed", "failure", "error"}:
        return "failure"
    if status in {"blocked", "denied"}:
        return "blocked"
    return status
