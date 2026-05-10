from __future__ import annotations

from typing import Any

from .config import Config


class RewardEngine:
    def __init__(self, config: Config | None = None):
        self.config = config

    def build_report(self, trajectory: dict[str, Any], test_results: list[dict[str, Any]]) -> dict[str, Any]:
        task_analysis = trajectory.get("task_analysis", {})
        actions = trajectory.get("actions", [])
        blocked = [action for action in actions if action.get("status") == "blocked"]
        failures = [action for action in actions if action.get("status") == "failure"]
        edits = [action for action in actions if action.get("action_type") == "edit_file" and action.get("status") == "success"]
        gate_actions = [action for action in actions if action.get("action_type") == "architecture_gate"]
        executor_attribution = _executor_attribution_summary(trajectory, actions)
        parallel_readonly = executor_attribution.get("parallel_readonly", {})
        parallel_readonly_issue = bool(
            parallel_readonly.get("failed_observation_count") or parallel_readonly.get("blocked_observation_count")
        )
        tests_run = bool(test_results)
        tests_passed = bool(test_results) and all(item.get("status") == "success" for item in test_results)
        if not test_results:
            tests_passed = None
        detected_tests = trajectory.get("environment_snapshot", {}).get("tests_detected", [])
        ui_sensitive = bool(task_analysis.get("ui_human_review_required"))
        architecture_sensitive = bool(gate_actions) or bool(task_analysis.get("architecture_gate_required"))
        spec_compliance = trajectory.get("spec_compliance") if isinstance(trajectory.get("spec_compliance"), dict) else {}
        spec_status = str(spec_compliance.get("status") or "unknown")
        spec_score = _score(spec_compliance.get("score"), default=None)
        spec_missing_count = len(spec_compliance.get("missing") or [])
        spec_violation_count = len(spec_compliance.get("violations") or [])
        spec_metric_missing_count = len(
            [item for item in spec_compliance.get("success_metric_coverage") or [] if not item.get("covered")]
        )
        scores = self._reward_scores()
        weights = self._reward_weights()
        thresholds = self._cost_thresholds()

        task_success = scores["default_task_success"]
        result_status = trajectory.get("result", {}).get("status")
        if result_status == "completed":
            task_success = scores["completed_with_edits"] if edits else scores["completed_without_edits"]
        elif result_status == "needs_human":
            task_success = scores["needs_human"]
        elif result_status == "failed":
            task_success = scores["failed"]
        if spec_compliance:
            if spec_status == "failed":
                task_success = min(task_success, 0.35)
            elif spec_status == "partial":
                task_success = round(task_success * (0.78 if spec_violation_count else 0.88), 3)

        process_safety = scores["safe_process"] if not blocked else scores["blocked_process"]
        if any(item.get("risk_level") == "high" for item in blocked):
            process_safety = scores["high_risk_blocked_process"]

        regression_status = "unknown"
        regression_score = scores["default_regression"]
        if tests_passed is True:
            regression_status = "passed"
            regression_score = scores["tests_passed"]
        elif tests_passed is False:
            regression_status = "failed"
            regression_score = scores["tests_failed"]
        elif not tests_run:
            if detected_tests:
                regression_status = "detected_not_run"
                regression_score = scores["tests_detected_not_run"]
            else:
                regression_status = "no_tests_available"
                regression_score = scores["no_tests_available"]

        cost = trajectory.get("cost", {})
        tool_calls = int(cost.get("tool_calls", 0) or 0)
        model_calls = int(cost.get("model_calls", 0) or 0)
        cost_score = scores["low_cost"]
        if tool_calls > thresholds["high_tool_calls"] or model_calls > thresholds["high_model_calls"]:
            cost_score = scores["high_cost"]
        elif tool_calls > thresholds["medium_tool_calls"] or model_calls > thresholds["medium_model_calls"]:
            cost_score = scores["medium_cost"]

        model_performance = trajectory.get("model_routing", {}).get("performance") or []
        memory_requested = self._memory_requested(str(trajectory.get("user_task", "")))
        experience_signals = {
            "edits": bool(edits),
            "failures": bool(failures),
            "blocked_actions": bool(blocked),
            "architecture_gate": bool(gate_actions),
            "ui_sensitive": ui_sensitive,
            "architecture_sensitive": architecture_sensitive,
            "model_performance": bool(model_performance),
            "memory_requested": memory_requested,
            "spec_compliance": bool(spec_compliance),
            "spec_compliance_gap": spec_status in {"partial", "failed"},
            "executor_attribution": executor_attribution.get("quality") not in {"none", "legacy_missing"},
            "parallel_readonly_issue": parallel_readonly_issue,
        }
        proposal_driving_signals = {
            key: value
            for key, value in experience_signals.items()
            if key not in {"executor_attribution"}
        }
        has_experience_signal = any(proposal_driving_signals.values())
        experience_value = scores["experience_with_signal"] if has_experience_signal else scores["experience_without_signal"]
        scope_control_score = self._scope_control_score(actions, edits, scores)
        silent_failure_signals = trajectory.get("silent_failure_signals") or []
        if spec_compliance and spec_status in {"partial", "failed"}:
            scope_control_score = round(scope_control_score * (0.62 if spec_violation_count else 0.82), 3)
        if silent_failure_signals:
            max_silent_risk = _max_signal_risk(silent_failure_signals)
            if max_silent_risk == "high":
                scope_control_score = round(scope_control_score * 0.65, 3)
            elif max_silent_risk == "medium":
                scope_control_score = round(scope_control_score * 0.8, 3)
            else:
                scope_control_score = round(scope_control_score * 0.92, 3)
        proposal_quality_score = round((experience_value * 0.6 + scope_control_score * 0.2 + process_safety * 0.2), 3)
        min_experience_score = self._float("reward", "min_experience_value_for_proposals", default=0.5)
        should_generate_experience = bool(experience_value >= min_experience_score or memory_requested)
        evidence_strength = self._evidence_strength(
            edits=bool(edits),
            failures=bool(failures),
            blocked=bool(blocked),
            tests_passed=tests_passed,
            gate_actions=bool(gate_actions),
            model_performance=bool(model_performance),
            memory_requested=memory_requested,
            spec_compliance=bool(spec_compliance),
            spec_compliance_gap=spec_status in {"partial", "failed"},
        )
        overall = round(
            task_success * weights["task_success"]
            + process_safety * weights["process_safety"]
            + regression_score * weights["regression"]
            + cost_score * weights["cost"]
            + experience_value * weights["experience_value"],
            3,
        )

        objective_reward = {
            "tests_passed": tests_passed,
            "commands_succeeded": not failures,
            "blocked_actions": len(blocked),
            "failed_actions": len(failures),
            "edited_files_count": len(edits),
            "process_safety": process_safety,
            "regression_risk": 1.0 - regression_score,
            "spec_compliance_status": spec_status if spec_compliance else None,
            "spec_compliance_score": spec_score,
            "spec_compliance_violations": spec_violation_count,
            "spec_compliance_missing": spec_missing_count,
            "score": overall
        }

        user_feedback_reward = trajectory.get(
            "user_feedback_reward",
            {"enabled": True, "active": False, "score": 0.0, "events": []},
        )
        llm_judge_reward = trajectory.get(
            "llm_judge_reward",
            {"enabled": bool(self.config.get("reward", "llm_judge", "enabled", default=False)) if self.config else False, "active": False, "score": 0.0},
        )

        reward_mode = self.config.get("reward", "mode", default="hybrid") if self.config else "hybrid"
        obj_w = self._float("reward", "weights", "objective", default=0.6)
        usr_w = self._float("reward", "weights", "user_feedback", default=0.3)
        llm_w = self._float("reward", "weights", "llm_judge", default=0.1)

        if reward_mode == "objective_plus_user":
            obj_w, usr_w, llm_w = 0.7, 0.3, 0.0
        elif reward_mode == "objective_only":
            obj_w, usr_w, llm_w = 1.0, 0.0, 0.0
        user_active = _component_active(user_feedback_reward)
        llm_active = _component_active(llm_judge_reward)
        final_components = _active_reward_components(
            objective_score=overall,
            user_score=float(user_feedback_reward.get("score", 0.0) or 0.0),
            llm_score=float(llm_judge_reward.get("score", 0.0) or 0.0),
            weights={"objective": obj_w, "user_feedback": usr_w, "llm_judge": llm_w},
            user_active=user_active and reward_mode != "objective_only",
            llm_active=llm_active and reward_mode == "hybrid",
        )
        hybrid_score = final_components["score"]

        notes: list[str] = []
        human_items = [
            "Review the generated diff for intent and scope.",
            "Confirm the original task is satisfied in the real project.",
            "Accept or reject generated experience proposals explicitly.",
        ]
        human_reasons = ["Durable evolution updates must be explicitly approved."]
        if tests_passed is True:
            notes.append("Configured tests/lint/build passed.")
        elif tests_passed is False:
            notes.append("At least one configured test/lint/build command failed.")
            human_reasons.append("Failed verification needs human repair or acceptance before learning from the run.")
        else:
            notes.append("No test/lint/build command was run; human or project-specific verification is still needed.")
            if detected_tests:
                human_reasons.append("The project has detected test commands that were not run.")
        if blocked:
            notes.append(f"{len(blocked)} action(s) were blocked by the safety layer.")
            human_reasons.append("Safety-blocked actions require explicit review; they must not be learned as normal workflow.")
        if failures:
            notes.append(f"{len(failures)} action(s) failed and may be reusable failure-pattern material.")
        if edits:
            notes.append("File edits were captured with diff and rollback backups.")
        if model_performance:
            notes.append("Model routing/performance signals were captured for future routing review.")
        if parallel_readonly.get("enabled"):
            notes.append(
                "Parallel read-only exploration was attributed separately from primary implementation actions."
            )
        if parallel_readonly_issue:
            notes.append("Parallel read-only exploration had failed or blocked sub-observations; inspect loaded context before learning from the run.")
            human_reasons.append("Exploration failures can make the agent under-informed even if the main task appears complete.")
        if spec_compliance:
            if spec_status == "full":
                notes.append("Spec compliance check passed for attached spec context.")
            else:
                notes.append(
                    "Spec compliance needs review: "
                    f"status={spec_status}, missing={spec_missing_count}, violations={spec_violation_count}, "
                    f"unverified_metrics={spec_metric_missing_count}."
                )
                human_reasons.append("Attached spec context was not fully satisfied; durable learning should avoid normalizing the gap.")
                human_items.append("Review spec compliance before accepting implementation-derived memory or skill proposals.")
        if silent_failure_signals:
            notes.append(f"{len(silent_failure_signals)} silent-failure risk signal(s) require inspection.")
            human_reasons.append("A run can appear successful while violating scope, verification, or governance expectations.")
        if ui_sensitive:
            notes.append("UX-sensitive work requires human confirmation of salience, feedback, and interaction feel.")
            human_reasons.append("Automated checks cannot fully verify visual prominence or perceived feedback.")
            human_items.extend(
                [
                    "Confirm the entry point is visible and understandable.",
                    "Confirm selected/hover/focus/disabled states are perceptible.",
                    "Check desktop and mobile layouts for overlap or hidden controls.",
                ]
            )
        if architecture_sensitive:
            notes.append("Architecture gate paused implementation before shared-contract edits.")
            human_reasons.append("Architecture-sensitive tasks require impact, migration, rollback, and validation review.")
            human_items.extend(
                [
                    "Review affected shared contracts and data flows.",
                    "Confirm the migration and rollback plan before edits.",
                ]
            )
        notes.append("Durable memory/skill/eval/rule updates require explicit user approval.")
        requires_human_review = True

        return {
            "schema_version": 1,
            "task_success": task_success,
            "execution_score": task_success,
            "process_safety": process_safety,
            "safety_score": process_safety,
            "regression_passed": tests_passed,
            "regression_status": regression_status,
            "regression_score": regression_score,
            "scope_control_score": scope_control_score,
            "cost_score": cost_score,
            "experience_value": experience_value,
            "experience_value_score": experience_value,
            "proposal_quality_score": proposal_quality_score,
            "silent_failure_signals": silent_failure_signals,
            "spec_compliance": spec_compliance,
            "objective_reward": objective_reward,
            "user_feedback_reward": user_feedback_reward,
            "llm_judge_reward": llm_judge_reward,
            "final_reward": final_components,
            "overall": hybrid_score,
            "objective_score_component": overall,
            "should_generate_experience": should_generate_experience,
            "experience_generation": {
                "should_generate_experience": should_generate_experience,
                "reason": self._experience_generation_reason(
                    should_generate=should_generate_experience,
                    evidence_strength=evidence_strength,
                    experience_value=experience_value,
                    min_experience_score=min_experience_score,
                    memory_requested=memory_requested,
                ),
                "evidence_strength": evidence_strength,
                "signals": experience_signals,
                "min_experience_score": min_experience_score,
            },
            "requires_human_review": requires_human_review,
            "objective_signals": {
                "tests_detected": detected_tests,
                "tests_run": tests_run,
                "tests_passed": tests_passed,
                "regression_status": regression_status,
                "edited_files": [
                    action.get("input", {}).get("path")
                    for action in edits
                    if action.get("input", {}).get("path")
                ],
                "edited_file_count": len(
                    [
                        action.get("input", {}).get("path")
                        for action in edits
                        if action.get("input", {}).get("path")
                    ]
                ),
                "blocked_actions": len(blocked),
                "failed_actions": len(failures),
                "architecture_gate_triggered": architecture_sensitive,
                "model_performance_signals": len(model_performance),
                "executor_attribution": executor_attribution,
                "silent_failure_signal_count": len(silent_failure_signals),
                "spec_compliance_status": spec_status if spec_compliance else None,
                "spec_compliance_score": spec_score,
                "spec_compliance_violation_count": spec_violation_count,
                "spec_compliance_missing_count": spec_missing_count,
                "spec_success_metric_missing_count": spec_metric_missing_count,
            },
            "llm_assisted_signals": {
                "enabled": bool(llm_judge_reward.get("enabled", False)),
                "notes": llm_judge_reward.get("notes", []),
                "reward_judge": llm_judge_reward,
            },
            "manual_signals": {
                "required": requires_human_review,
                "reasons": human_reasons,
                "items": human_items,
                "user_feedback": user_feedback_reward,
            },
            "signals": {
                "actions": len(actions),
                "edits": len(edits),
                "blocked_actions": len(blocked),
                "failed_actions": len(failures),
                "tests_run": tests_run,
                "ui_sensitive": ui_sensitive,
                "architecture_sensitive": architecture_sensitive,
                "experience_signals": experience_signals,
                "executor_attribution_quality": executor_attribution.get("quality"),
            },
            "test_results": test_results,
            "config": {
                "weights": weights,
                "hybrid_weights": {
                    "objective": obj_w,
                    "user_feedback": usr_w,
                    "llm_judge": llm_w,
                },
                "cost_thresholds": thresholds,
            },
            "human_acceptance": {
                "required": requires_human_review,
                "items": human_items,
            },
            "notes": notes,
        }

    def _reward_weights(self) -> dict[str, float]:
        defaults = {
            "task_success": 0.30,
            "process_safety": 0.20,
            "regression": 0.25,
            "cost": 0.10,
            "experience_value": 0.15,
        }
        return {key: self._float("reward", "weights", key, default=value) for key, value in defaults.items()}

    def _reward_scores(self) -> dict[str, float]:
        defaults = {
            "default_task_success": 0.60,
            "completed_with_edits": 0.80,
            "completed_without_edits": 0.55,
            "needs_human": 0.45,
            "failed": 0.20,
            "safe_process": 1.0,
            "blocked_process": 0.55,
            "high_risk_blocked_process": 0.35,
            "tests_passed": 1.0,
            "tests_failed": 0.15,
            "tests_detected_not_run": 0.45,
            "no_tests_available": 0.70,
            "default_regression": 0.50,
            "low_cost": 1.0,
            "medium_cost": 0.75,
            "high_cost": 0.55,
            "experience_with_signal": 0.75,
            "experience_without_signal": 0.45,
            "scope_control_default": 0.75,
            "scope_control_no_edits": 0.70,
            "scope_control_broad_edits": 0.45,
            "scope_control_failed_or_blocked": 0.55,
        }
        return {key: self._float("reward", "scores", key, default=value) for key, value in defaults.items()}

    def _cost_thresholds(self) -> dict[str, int]:
        defaults = {
            "medium_tool_calls": 12,
            "high_tool_calls": 20,
            "medium_model_calls": 8,
            "high_model_calls": 12,
        }
        return {key: self._int("reward", "cost_thresholds", key, default=value) for key, value in defaults.items()}

    def _float(self, *keys: str, default: float) -> float:
        if not self.config:
            return default
        try:
            return float(self.config.get(*keys, default=default))
        except (TypeError, ValueError):
            return default

    def _int(self, *keys: str, default: int) -> int:
        if not self.config:
            return default
        try:
            return int(self.config.get(*keys, default=default))
        except (TypeError, ValueError):
            return default

    def _scope_control_score(
        self,
        actions: list[dict[str, Any]],
        edits: list[dict[str, Any]],
        scores: dict[str, float],
    ) -> float:
        if any(action.get("status") in {"blocked", "failure"} for action in actions):
            return scores["scope_control_failed_or_blocked"]
        if not edits:
            return scores["scope_control_no_edits"]
        edited_paths = [
            str(action.get("input", {}).get("path") or "")
            for action in edits
            if action.get("input", {}).get("path")
        ]
        top_levels = {path.split("/", 1)[0] for path in edited_paths if path}
        broad_threshold = self._int("reward", "scope", "broad_edit_top_level_threshold", default=4)
        if len(top_levels) >= broad_threshold:
            return scores["scope_control_broad_edits"]
        return scores["scope_control_default"]

    def _evidence_strength(
        self,
        *,
        edits: bool,
        failures: bool,
        blocked: bool,
        tests_passed: bool | None,
        gate_actions: bool,
        model_performance: bool,
        memory_requested: bool,
        spec_compliance: bool,
        spec_compliance_gap: bool,
    ) -> str:
        if spec_compliance and not spec_compliance_gap and tests_passed is True and edits:
            return "high"
        if tests_passed is True and (edits or failures or gate_actions):
            return "high"
        if failures or blocked or gate_actions or edits or model_performance or spec_compliance:
            return "medium"
        if memory_requested:
            return "medium"
        return "low"

    def _experience_generation_reason(
        self,
        *,
        should_generate: bool,
        evidence_strength: str,
        experience_value: float,
        min_experience_score: float,
        memory_requested: bool,
    ) -> str:
        if should_generate:
            if memory_requested:
                return "The task explicitly asks Praxile to remember or record project context."
            return (
                f"Reusable experience signal is {evidence_strength}; "
                f"experience_value={experience_value} meets threshold {min_experience_score}."
            )
        return (
            f"Experience signal is too weak for durable proposals "
            f"(experience_value={experience_value}, threshold={min_experience_score})."
        )

    def _memory_requested(self, task: str) -> bool:
        lower = task.lower()
        markers = [
            "remember",
            "record",
            "note",
            "capture",
            "记住",
            "记录",
            "沉淀",
            "写入 memory",
            "项目上下文",
        ]
        return any(marker in lower for marker in markers)


def _executor_attribution_summary(trajectory: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    registered: dict[str, dict[str, Any]] = {}
    for item in trajectory.get("executors") or []:
        if not isinstance(item, dict):
            continue
        executor_id = str(item.get("executor_id") or "").strip()
        if not executor_id:
            continue
        registered[executor_id] = {
            "executor_id": executor_id,
            "kind": str(item.get("kind") or "unknown"),
            "role": str(item.get("role") or ""),
            "parent_executor_id": item.get("parent_executor_id"),
        }

    action_counts: dict[str, int] = {}
    failure_counts: dict[str, int] = {}
    blocked_counts: dict[str, int] = {}
    unattributed_actions = 0
    worker_events: dict[str, dict[str, Any]] = {}
    parallel_statuses: list[str] = []
    parallel_action_count = 0
    parallel_max_concurrency = None

    for action in actions:
        executor = action.get("executor") if isinstance(action.get("executor"), dict) else {}
        executor_id = str(executor.get("executor_id") or "").strip()
        if executor_id:
            registered.setdefault(
                executor_id,
                {
                    "executor_id": executor_id,
                    "kind": str(executor.get("kind") or "unknown"),
                    "role": str(executor.get("role") or ""),
                    "parent_executor_id": executor.get("parent_executor_id"),
                },
            )
            action_counts[executor_id] = action_counts.get(executor_id, 0) + 1
            if action.get("status") == "failure":
                failure_counts[executor_id] = failure_counts.get(executor_id, 0) + 1
            if action.get("status") == "blocked":
                blocked_counts[executor_id] = blocked_counts.get(executor_id, 0) + 1
        else:
            unattributed_actions += 1

        observation = action.get("observation") if isinstance(action.get("observation"), dict) else {}
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        for event in data.get("executor_events") or []:
            if not isinstance(event, dict):
                continue
            worker_id = str(event.get("executor_id") or "").strip()
            if not worker_id:
                continue
            worker_events[worker_id] = {
                "executor_id": worker_id,
                "kind": str(event.get("kind") or "readonly_worker"),
                "role": str(event.get("role") or "read_only"),
                "parent_executor_id": event.get("parent_executor_id"),
            }
            registered.setdefault(worker_id, worker_events[worker_id])
        if action.get("action_type") == "parallel_readonly_exploration":
            parallel_action_count += int(data.get("count") or 0)
            parallel_max_concurrency = data.get("max_concurrency", parallel_max_concurrency)
            for item in data.get("observations") or []:
                if isinstance(item, dict):
                    parallel_statuses.append(str(item.get("status") or "unknown"))

    total_actions = len(actions)
    if not total_actions and not registered:
        quality = "none"
    elif unattributed_actions == 0 and total_actions:
        quality = "complete"
    elif unattributed_actions < total_actions:
        quality = "partial"
    else:
        quality = "legacy_missing"

    parallel_summary = dict(trajectory.get("parallel_readonly_exploration") or {})
    parallel_summary.update(
        {
            "enabled": bool(parallel_summary.get("enabled")) or bool(worker_events),
            "worker_count": len(worker_events),
            "subaction_count": parallel_action_count or parallel_summary.get("action_count", 0),
            "failed_observation_count": sum(1 for status in parallel_statuses if status == "failure"),
            "blocked_observation_count": sum(1 for status in parallel_statuses if status == "blocked"),
            "observation_statuses": parallel_statuses,
        }
    )
    if parallel_max_concurrency is not None:
        parallel_summary["max_concurrency"] = parallel_max_concurrency

    return {
        "quality": quality,
        "registered_executor_count": len(registered),
        "action_executor_counts": action_counts,
        "failed_actions_by_executor": failure_counts,
        "blocked_actions_by_executor": blocked_counts,
        "unattributed_action_count": unattributed_actions,
        "executors": list(registered.values())[:24],
        "parallel_readonly": parallel_summary,
    }


def _component_active(component: dict[str, Any]) -> bool:
    if component.get("active") is True:
        return True
    if component.get("events"):
        return True
    try:
        return float(component.get("score", 0.0) or 0.0) > 0.0 and "score" in component
    except (TypeError, ValueError):
        return False


def _max_signal_risk(signals: list[dict[str, Any]]) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return max((str(item.get("risk") or "low") for item in signals), key=lambda item: order.get(item, 0), default="low")


def _score(value: Any, *, default: float | None) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, parsed)), 4)


def _active_reward_components(
    *,
    objective_score: float,
    user_score: float,
    llm_score: float,
    weights: dict[str, float],
    user_active: bool,
    llm_active: bool,
) -> dict[str, Any]:
    components = [
        {"name": "objective", "score": round(objective_score, 3), "configured_weight": weights["objective"], "active": True},
        {
            "name": "user_feedback",
            "score": round(user_score, 3),
            "configured_weight": weights["user_feedback"],
            "active": user_active,
        },
        {
            "name": "llm_judge",
            "score": round(llm_score, 3),
            "configured_weight": weights["llm_judge"],
            "active": llm_active,
        },
    ]
    active = [item for item in components if item["active"] and float(item["configured_weight"] or 0.0) > 0.0]
    total = sum(float(item["configured_weight"]) for item in active)
    if total <= 0:
        return {
            "score": round(objective_score, 3),
            "mode": "objective_fallback",
            "components": components,
            "effective_weights": {"objective": 1.0, "user_feedback": 0.0, "llm_judge": 0.0},
        }
    score = 0.0
    effective: dict[str, float] = {}
    for item in components:
        if item in active:
            weight = float(item["configured_weight"]) / total
        else:
            weight = 0.0
        effective[item["name"]] = round(weight, 4)
        score += float(item["score"]) * weight
    return {
        "score": round(score, 3),
        "mode": "weighted_active_components",
        "components": components,
        "effective_weights": effective,
    }
