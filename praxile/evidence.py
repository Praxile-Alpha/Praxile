from __future__ import annotations

from typing import Any

from .utils import shorten


class EvidenceExtractor:
    """
    Extracts structured evidence from a single task trajectory.
    """

    @staticmethod
    def extract(trajectory: dict[str, Any]) -> dict[str, Any]:
        from .evolution import (
            _edited_paths,
            _failed_commands,
            _verification_commands,
            _failure_excerpts,
            _failure_signature,
        )
        task_id = trajectory.get("task_id", "unknown")
        task_type = trajectory.get("task_analysis", {}).get("task_type", "unknown")
        intent = trajectory.get("user_task", "")
        
        # Files
        touched_files = _edited_paths(trajectory)
        read_files = EvidenceExtractor._read_files(trajectory)
        
        # Commands
        failed_commands = _failed_commands(trajectory)
        passed_commands = _verification_commands(trajectory)
        commands = EvidenceExtractor._extract_commands(trajectory)
        
        # Failures
        excerpts = _failure_excerpts(trajectory)
        failure_signatures = [_failure_signature(ex) for ex in excerpts]
        
        # Fixes
        # We try to extract a structured dict from the _fix_action_lines
        fix_actions = EvidenceExtractor._extract_fix_actions(trajectory)
        
        # Assets
        loaded_assets = EvidenceExtractor._loaded_assets(trajectory)
            
        blocked_actions = [
            action for action in trajectory.get("actions", [])
            if action.get("status") == "blocked"
        ]
        
        # Reward & Outcome
        reward = trajectory.get("reward_report", {})
        outcome = trajectory.get("result", {}).get("status", "unknown")
        executor_attribution = EvidenceExtractor._executor_attribution(trajectory, reward)

        return {
            "task_id": task_id,
            "task_type": task_type,
            "intent": intent,
            "touched_files": touched_files,
            "read_files": read_files,
            "failed_commands": failed_commands,
            "passed_commands": passed_commands,
            "verification_commands": passed_commands,
            "commands": commands,
            "failure_excerpts": excerpts,
            "failure_signatures": list(dict.fromkeys(failure_signatures)),
            "fix_actions": fix_actions,
            "loaded_assets": loaded_assets,
            "accepted_proposals": EvidenceExtractor._proposal_summaries(trajectory, status="accepted"),
            "rejected_proposals": EvidenceExtractor._proposal_summaries(trajectory, status="rejected"),
            "user_feedback": EvidenceExtractor._feedback_events(trajectory),
            "blocked_actions": blocked_actions,
            "executor_attribution": executor_attribution,
            "executors": executor_attribution.get("executors", []),
            "reward": {
                "overall": reward.get("overall", 0.0),
                "task_success": reward.get("task_success"),
                "regression_passed": reward.get("regression_passed"),
                "regression_status": reward.get("regression_status"),
                "tests_passed": any(item.get("status") == "success" for item in reward.get("test_results", [])),
                "process_safety": reward.get("process_safety", 1.0),
                "cost_score": reward.get("cost_score"),
                "experience_value": reward.get("experience_value"),
                "objective_reward": reward.get("objective_reward", {}),
                "final_reward": reward.get("final_reward", {}),
            },
            "diff_summary": EvidenceExtractor._diff_summary(trajectory, fix_actions),
            "outcome": outcome
        }

    @staticmethod
    def _read_files(trajectory: dict[str, Any]) -> list[str]:
        read_files = []
        for action in trajectory.get("actions", []):
            if action.get("action_type") in {"read_file", "search_files"} and action.get("status") == "success":
                path = action.get("input", {}).get("path")
                if path:
                    read_files.append(str(path))
        return list(dict.fromkeys(read_files))

    @staticmethod
    def _extract_fix_actions(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        fixes = []
        for action in trajectory.get("actions", []):
            if action.get("action_type") == "edit_file" and action.get("status") == "success":
                path = action.get("input", {}).get("path")
                if path:
                    observation = action.get("observation", {}) or {}
                    data = observation.get("data", {}) if isinstance(observation.get("data"), dict) else {}
                    diff = str(data.get("diff") or observation.get("output") or "")
                    insertions, deletions = EvidenceExtractor._diff_counts(diff)
                    changed = bool(data.get("changed", True))
                    if changed and (insertions or deletions):
                        summary = f"edited `{path}` (+{insertions}/-{deletions})"
                    elif changed:
                        summary = f"edited `{path}`"
                    else:
                        summary = f"checked `{path}` without content changes"
                    fixes.append({
                        "file": str(path),
                        "path": str(path),
                        "summary": summary,
                        "insertions": insertions,
                        "deletions": deletions,
                        "changed": changed,
                        "backup_path": data.get("backup_path"),
                        "step": action.get("step"),
                        "diff_excerpt": shorten(diff, 1200) if diff else "",
                    })
        return fixes

    @staticmethod
    def _extract_commands(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in trajectory.get("reward_report", {}).get("test_results", []) or []:
            data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
            command = data.get("command")
            if not command:
                continue
            status = str(item.get("status") or "unknown")
            key = (str(command), status)
            if key in seen:
                continue
            seen.add(key)
            commands.append(
                {
                    "command": str(command),
                    "status": status,
                    "action_type": "test_result",
                    "output_excerpt": shorten(str(item.get("output") or ""), 600),
                }
            )
        for action in trajectory.get("actions", []):
            if action.get("action_type") not in {"run_command", "run_test"}:
                continue
            observation = action.get("observation", {}) or {}
            data = observation.get("data", {}) if isinstance(observation.get("data"), dict) else {}
            command = action.get("input", {}).get("command") or data.get("command")
            if not command:
                continue
            status = str(action.get("status") or observation.get("status") or "unknown")
            key = (str(command), status)
            if key in seen:
                continue
            seen.add(key)
            commands.append(
                {
                    "command": str(command),
                    "status": status,
                    "action_type": str(action.get("action_type") or "run_command"),
                    "step": action.get("step"),
                    "output_excerpt": shorten(str(observation.get("output") or ""), 600),
                }
            )
        return commands

    @staticmethod
    def _loaded_assets(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        values = []
        values.extend(trajectory.get("loaded_assets") or [])
        values.extend(
            trajectory.get("reward_report", {})
            .get("experience_generation", {})
            .get("loaded_assets", [])
            or []
        )
        loaded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in values:
            if isinstance(item, dict):
                key = str(item.get("path") or item.get("asset_id") or item)
                if key in seen:
                    continue
                seen.add(key)
                loaded.append(
                    {
                        "path": item.get("path") or item.get("asset_id"),
                        "type": item.get("type") or item.get("asset_type") or item.get("kind"),
                        "score": item.get("final_score", item.get("score")),
                        "why_loaded": item.get("why_loaded") or item.get("reason"),
                        "matched_terms": item.get("matched_terms") or [],
                    }
                )
            elif item:
                key = str(item)
                if key not in seen:
                    seen.add(key)
                    loaded.append({"path": key, "type": "unknown"})
        return loaded

    @staticmethod
    def _diff_counts(diff: str) -> tuple[int, int]:
        insertions = 0
        deletions = 0
        for line in str(diff or "").splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                insertions += 1
            elif line.startswith("-"):
                deletions += 1
        return insertions, deletions

    @staticmethod
    def _diff_summary(trajectory: dict[str, Any], fix_actions: list[dict[str, Any]]) -> dict[str, Any]:
        summary = dict(trajectory.get("diff_summary") or {})
        if fix_actions:
            summary.setdefault("files_changed", len({item.get("file") for item in fix_actions if item.get("file")}))
            summary.setdefault("insertions", sum(int(item.get("insertions") or 0) for item in fix_actions))
            summary.setdefault("deletions", sum(int(item.get("deletions") or 0) for item in fix_actions))
            excerpts = [item.get("diff_excerpt") for item in fix_actions if item.get("diff_excerpt")]
            if excerpts and not summary.get("diff_excerpt"):
                summary["diff_excerpt"] = "\n\n".join(excerpts[:3])
        return summary

    @staticmethod
    def _proposal_summaries(trajectory: dict[str, Any], *, status: str) -> list[dict[str, Any]]:
        values = []
        values.extend(trajectory.get("experience_candidates") or [])
        values.extend(trajectory.get("proposals") or [])
        result: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            item_status = str(item.get("status") or "")
            if item_status and item_status != status:
                continue
            if not item_status and status != "accepted":
                continue
            result.append(
                {
                    "proposal_id": item.get("proposal_id"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "reason": item.get("reason"),
                    "confidence": item.get("confidence"),
                }
            )
        return result

    @staticmethod
    def _executor_attribution(trajectory: dict[str, Any], reward: dict[str, Any]) -> dict[str, Any]:
        objective = reward.get("objective_signals", {}) if isinstance(reward.get("objective_signals"), dict) else {}
        attribution = objective.get("executor_attribution")
        if isinstance(attribution, dict) and attribution:
            return attribution

        executors: dict[str, dict[str, Any]] = {}
        for item in trajectory.get("executors") or []:
            if not isinstance(item, dict):
                continue
            executor_id = str(item.get("executor_id") or "").strip()
            if not executor_id:
                continue
            executors[executor_id] = {
                "executor_id": executor_id,
                "kind": item.get("kind") or "unknown",
                "role": item.get("role") or "",
                "parent_executor_id": item.get("parent_executor_id"),
            }
        action_counts: dict[str, int] = {}
        missing = 0
        for action in trajectory.get("actions", []) or []:
            executor = action.get("executor") if isinstance(action.get("executor"), dict) else {}
            executor_id = str(executor.get("executor_id") or "").strip()
            if not executor_id:
                missing += 1
                continue
            action_counts[executor_id] = action_counts.get(executor_id, 0) + 1
            executors.setdefault(
                executor_id,
                {
                    "executor_id": executor_id,
                    "kind": executor.get("kind") or "unknown",
                    "role": executor.get("role") or "",
                    "parent_executor_id": executor.get("parent_executor_id"),
                },
            )
        return {
            "quality": "complete" if action_counts and not missing else ("partial" if action_counts else "legacy_missing"),
            "registered_executor_count": len(executors),
            "action_executor_counts": action_counts,
            "unattributed_action_count": missing,
            "executors": list(executors.values()),
            "parallel_readonly": trajectory.get("parallel_readonly_exploration") or {},
        }

    @staticmethod
    def _feedback_events(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
        values = []
        reward = trajectory.get("reward_report", {}) if isinstance(trajectory.get("reward_report"), dict) else {}
        values.extend(trajectory.get("user_feedback") or [])
        values.extend(trajectory.get("feedback") or [])
        values.extend(reward.get("user_feedback_reward", {}).get("events", []) if isinstance(reward.get("user_feedback_reward"), dict) else [])
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            key = str(item.get("feedback_id") or item)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "feedback_id": item.get("feedback_id"),
                    "target_type": item.get("target_type"),
                    "target_id": item.get("target_id"),
                    "sentiment": item.get("sentiment"),
                    "strength": item.get("strength"),
                    "feedback_type": item.get("feedback_type"),
                    "raw_text": item.get("raw_text"),
                }
            )
        return result
