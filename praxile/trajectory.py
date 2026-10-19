from __future__ import annotations

from typing import Any
from copy import deepcopy

from .utils import new_id, utc_now
from .subtasks import SubtaskEpisode


class TrajectoryLogger:
    def __init__(self, user_task: str, environment_snapshot: dict[str, Any]):
        self.data: dict[str, Any] = {
            "task_id": new_id("task"),
            "user_task": user_task,
            "start_time": utc_now(),
            "end_time": None,
            "environment_snapshot": environment_snapshot,
            "loaded_memories": [],
            "loaded_skills": [],
            "loaded_rules": [],
            "loaded_assets": [],
            "spec_context": {},
            "task_analysis": {},
            "plan": [],
            "executors": [],
            "actions": [],
            "artifacts": [],
            "diff_summary": {},
            "cost": {
                "model_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tool_calls": 0,
            },
            "model_routing": {
                "selected": None,
                "calls": [],
                "performance": [],
            },
            "result": {
                "status": "running",
                "summary": "",
            },
            "reward_report": {},
            "experience_candidates": [],
        }

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "TrajectoryLogger":
        logger = cls.__new__(cls)
        logger.data = data
        return logger

    @property
    def task_id(self) -> str:
        return self.data["task_id"]

    def set_loaded_context(self, context: list[dict[str, Any]], *, injected_paths: set[str] | None = None) -> None:
        self.data["loaded_memories"] = [item for item in context if item.get("kind") == "memory"]
        self.data["loaded_skills"] = [item for item in context if item.get("kind") == "skill"]
        self.data["loaded_rules"] = [item for item in context if item.get("kind") == "rule"]
        self.data["loaded_assets"] = [
            {
                "asset_id": item.get("path"),
                "asset_type": item.get("type") or item.get("kind"),
                "kind": item.get("kind"),
                "path": item.get("path"),
                "score": item.get("final_score", item.get("score")),
                "matched_terms": item.get("matched_terms") or [],
                "matched_fields": item.get("matched_fields") or [],
                "why_loaded": item.get("why_loaded") or item.get("reason"),
                "used_in_prompt": injected_paths is None or item.get("path") in injected_paths,
                "source_task_id": item.get("source_task_id"),
                "confidence": item.get("confidence"),
            }
            for item in context
        ]

    def mark_context_injected(self, paths: set[str]) -> None:
        for item in self.data.get("loaded_assets", []):
            if item.get("path") in paths:
                item["used_in_prompt"] = True

    def set_plan(self, plan: list[str]) -> None:
        self.data["plan"] = plan

    def set_task_analysis(self, analysis: dict[str, Any]) -> None:
        self.data["task_analysis"] = analysis

    def set_spec_context(self, context: dict[str, Any]) -> None:
        self.data["spec_context"] = context

    def register_executor(
        self,
        executor_id: str,
        *,
        kind: str,
        role: str | None = None,
        description: str | None = None,
        parent_executor_id: str | None = None,
    ) -> None:
        if not executor_id:
            return
        executors = self.data.setdefault("executors", [])
        existing = next((item for item in executors if item.get("executor_id") == executor_id), None)
        payload = {
            "executor_id": executor_id,
            "kind": kind,
            "role": role,
            "description": description,
            "parent_executor_id": parent_executor_id,
            "registered_at": utc_now(),
        }
        if existing:
            existing.update({key: value for key, value in payload.items() if value is not None})
        else:
            executors.append(payload)

    def add_action(
        self,
        *,
        action_type: str,
        input_data: dict[str, Any],
        observation: dict[str, Any],
        status: str,
        cost: dict[str, Any] | None = None,
        executor: dict[str, Any] | None = None,
    ) -> None:
        action_record = {
            "action_id": new_id("action"),
            "step": len(self.data["actions"]) + 1,
            "action_type": action_type,
            "input": input_data,
            "observation": observation,
            "status": status,
            "cost": cost or {},
            "created_at": utc_now(),
        }
        if executor:
            action_record["executor"] = executor
        self.data["actions"].append(action_record)
        active = self._active_subtask()
        if active is not None and action_type not in {"subtask_begin", "subtask_end"}:
            active["action_refs"].append(action_record["action_id"])
            counts = active["observed_status_counts"]
            counts[status] = counts.get(status, 0) + 1
            artifact = (observation.get("data") or {}).get("artifact")
            if isinstance(artifact, str) and artifact not in active["artifacts"]:
                active["artifacts"].append(artifact)
        if action_type not in {
            "subtask_begin",
            "subtask_end",
            "subtask_note",
            "subtask_reflect",
            "architecture_gate",
            "dry_run_skip_tests",
            "run_test",
            "model_unavailable",
            "model_response",
            "finish",
        }:
            self.data["cost"]["tool_calls"] += 1

    def _active_subtask(self) -> dict[str, Any] | None:
        return next((episode for episode in self.data.get("subtask_episodes", [])
                     if episode.get("status") == "running"), None)

    def begin_subtask(self, goal: str, snapshot: dict[str, Any]) -> str:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 2000:
            raise ValueError("subtask goal must contain 1 to 2000 characters")
        if self._active_subtask() is not None:
            raise ValueError("end the current subtask before beginning another")
        episode = SubtaskEpisode(new_id("subtask"), self.task_id, goal.strip(), deepcopy(snapshot), utc_now())
        self.data.setdefault("subtask_episodes", []).append(episode.to_dict())
        return episode.episode_id

    def end_subtask(self, *, claimed_outcome: str, summary: str, snapshot: dict[str, Any]) -> str:
        active = self._active_subtask()
        if active is None:
            raise ValueError("no running subtask")
        if claimed_outcome not in {"completed", "failed", "needs_human"}:
            raise ValueError("invalid claimed subtask outcome")
        if not isinstance(summary, str) or len(summary) > 4000:
            raise ValueError("subtask summary must be at most 4000 characters")
        active.update(status="closed", claimed_outcome=claimed_outcome, summary=summary,
                      state_after=deepcopy(snapshot), ended_at=utc_now())
        return active["episode_id"]

    def set_model_route(self, route: dict[str, Any]) -> None:
        self.data["model_routing"]["selected"] = route

    def add_model_cost(self, usage: dict[str, Any], *, route: dict[str, Any] | None = None, status: str = "success") -> None:
        self.data["cost"]["model_calls"] += 1
        self.data["cost"]["prompt_tokens"] += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        self.data["cost"]["completion_tokens"] += int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        )
        call = {
            "status": status,
            "usage": usage,
            "created_at": utc_now(),
        }
        if route:
            call.update(route)
        self.data["model_routing"]["calls"].append(call)

    def add_model_performance(self, event: dict[str, Any]) -> None:
        self.data["model_routing"]["performance"].append({"created_at": utc_now(), **event})

    def set_diff_summary(self, diff_summary: dict[str, Any]) -> None:
        self.data["diff_summary"] = diff_summary

    def add_artifact(self, artifact: dict[str, Any]) -> None:
        self.data["artifacts"].append(artifact)

    def set_reward_report(self, report: dict[str, Any]) -> None:
        self.data["reward_report"] = report

    def set_experience_candidates(self, candidates: list[dict[str, Any]]) -> None:
        self.data["experience_candidates"] = candidates

    def finish(self, *, status: str, summary: str) -> dict[str, Any]:
        active = self._active_subtask()
        if active is not None:
            active.update(status="interrupted", ended_at=utc_now())
            active["uncertainty"].append("Run ended without an explicit subtask boundary; final subtask state is unknown.")
        self.data["end_time"] = utc_now()
        self.data["result"] = {"status": status, "summary": summary}
        return self.data
