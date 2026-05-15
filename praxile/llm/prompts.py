from __future__ import annotations

import json
from typing import Any


PROPOSAL_GENERATION_PROMPT = """
You are helping Praxile extract reusable engineering experience from an agent trajectory.

Given the trajectory below, identify reusable memories, skills, eval cases,
failure patterns, harness rules, or routing notes.

Requirements:
- Only extract patterns that can be reused in future tasks.
- Avoid overfitting to one-off details.
- Each proposal must include: type, title, reason, evidence, confidence,
  applicability_scope, anti_scope, and changes.
- Evidence must cite concrete trajectory signals.
- Output valid JSON as either a list or {{"proposals": [...]}}.
- Do not propose secrets, hidden files, safety bypasses, architecture gates,
  frozen boundaries, or direct config mutations.

Allowed types:
memory_update, skill_create, eval_case, failure_pattern, harness_rule, routing

Allowed change roots:
memory/, skills/, evals/checklists/, evals/regression-cases/,
experience/failures/, experience/patterns/, rules/harness-rules/

Trajectory:
{trajectory}
""".strip()


def build_proposal_generation_messages(trajectory: dict[str, Any]) -> list[dict[str, str]]:
    compact = {
        "task_id": trajectory.get("task_id"),
        "user_task": trajectory.get("user_task"),
        "task_analysis": trajectory.get("task_analysis"),
        "result": trajectory.get("result"),
        "reward_report": trajectory.get("reward_report"),
        "actions": [
            {
                "step": action.get("step"),
                "action_type": action.get("action_type"),
                "status": action.get("status"),
                "executor": action.get("executor"),
                "output": str((action.get("observation") or {}).get("output", ""))[:500]
                if isinstance(action.get("observation"), dict)
                else "",
            }
            for action in trajectory.get("actions", [])[:12]
            if isinstance(action, dict)
        ],
        "executor_attribution": _executor_attribution(trajectory),
    }
    return [
        {
            "role": "system",
            "content": (
                "You propose optional Praxile experience assets from one trajectory. "
                "All output is pending human approval and must be valid JSON only."
            ),
        },
        {
            "role": "user",
            "content": PROPOSAL_GENERATION_PROMPT.format(
                trajectory=json.dumps(compact, ensure_ascii=False)
            ),
        },
    ]


def _executor_attribution(trajectory: dict[str, Any]) -> dict[str, Any] | None:
    report = trajectory.get("reward_report") if isinstance(trajectory.get("reward_report"), dict) else {}
    objective = report.get("objective_signals") if isinstance(report.get("objective_signals"), dict) else {}
    attribution = objective.get("executor_attribution") if isinstance(objective.get("executor_attribution"), dict) else None
    return attribution
