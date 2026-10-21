from __future__ import annotations

import json
import hashlib
import re
import time
from collections.abc import Callable
from typing import Any

from .action_schema import ActionSchemaRegistry
from .action_protocol import decode_action
from .config import Config
from .environment import FileSystemEnv, GitEnv, ProjectEnv, ShellEnv, TestEnv
from .evolution import EvolutionEngine
from .experience_context import RuntimeExperienceContext
from .experience_conditions import check_conditions, observe_tool_versions
from .task_notes import add_note, select_notes
from .subtask_reflection import reflect_subtask
from .interop import interop_policy
from .judge_governance import JudgeGovernance
from .json_utils import RobustJSONError, parse_json_object
from .model import ModelRequestCancelled, ModelRouter, ModelUnavailable
from .reward import RewardEngine
from .security import SafetyPolicy
from .semantic_judges import AttributionJudge
from .services.context_juice_service import ContextJuiceService
from .silent_failure import detect_silent_failure_signals
from .specs import build_spec_context, spec_context_prompt, verify_spec_compliance
from .store import ExperienceStore
from .task_analyzer import TaskAnalyzer
from .task_input import TaskInput
from .tools import MUTATING_ACTIONS, ToolRegistry
from .trajectory import TrajectoryLogger
from .utils import append_jsonl, shorten, utc_now


ProgressCallback = Callable[[str, str, Any], None]


class AgentRuntime:
    def __init__(self, config: Config):
        self.config = config
        self.store = ExperienceStore(config.paths)
        self.safety = SafetyPolicy(config)
        self.fs = FileSystemEnv(config, self.safety)
        self.git = GitEnv(config)
        self.shell = ShellEnv(config, self.safety)
        self.tests = TestEnv(config, self.shell)
        self.project = ProjectEnv(config, self.fs, self.git, self.tests)
        self.tools = ToolRegistry(config, fs=self.fs, git=self.git, shell=self.shell, tests=self.tests)
        self.reward = RewardEngine(config)
        self.router = ModelRouter(config)
        self.evolution = EvolutionEngine(config, router=self.router)
        self.analyzer = TaskAnalyzer(config, router=self.router)
        self._trace_cleanup_done = False
        self.action_schemas = ActionSchemaRegistry()
        self.primary_executor_id = str(
            config.get("executors", "primary_executor_id", default="coding_agent") or "coding_agent"
        )

    def run(
        self,
        task: str | TaskInput,
        *,
        test_commands: list[str] | None = None,
        max_steps: int | None = None,
        dry_run: bool = False,
        resume: str | None = None,
        spec_files: list[str] | None = None,
        parallel_readonly_explore: bool | None = None,
        use_experience: bool = True,
        experimental_context: str | None = None,
        deliverable_contract: dict | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        try:
            if experimental_context is not None:
                if (resume or use_experience or not isinstance(experimental_context, str)
                        or not experimental_context.strip() or len(experimental_context) > 6000):
                    raise ValueError("experimental context requires a fresh experience-disabled run and 1..6000 characters")
            task_input = task if isinstance(task, TaskInput) else None
            if task_input is not None and resume:
                raise ValueError("structured task input cannot override a resumed run")
            intent = task_input.body if task_input else task
            if task_input:
                task = task_input.prompt()
            self.tools.bind_deliverable_contract(deliverable_contract)
            self.tools.cancel_requested = cancel_requested
            self._emit_progress(progress_callback, "initializing", "Initializing Praxile runtime.")
            self.store.initialize(self.config)
            if resume:
                return self._resume_run(
                    resume,
                    task_override=task or None,
                    test_commands=test_commands,
                    max_steps=max_steps,
                    dry_run=dry_run,
                )

            self._emit_progress(progress_callback, "snapshot", "Capturing project snapshot.")
            snapshot = self.project.snapshot(refresh=True)
            snapshot["interop"] = interop_policy(self.config)
            logger = TrajectoryLogger(task, snapshot)
            if experimental_context is not None:
                logger.data["experimental_context"] = {
                    "content": experimental_context,
                    "sha256": hashlib.sha256(experimental_context.encode()).hexdigest(),
                    "injected": False, "persisted_as_asset": False, "permission_granted": False}
            if task_input:
                logger.data["task_input"] = task_input.record()
            if self.tools.deliverable_contract is not None:
                logger.data["deliverable_contract"] = self.tools.deliverable_contract
                logger.data["deliverable_contract_sha256"] = hashlib.sha256(json.dumps(
                    self.tools.deliverable_contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            self._register_base_executors(logger)
            logger.data["dry_run"] = dry_run
            logger.data["experience_control"] = {
                "mode": "enabled" if use_experience else "withheld",
                "purpose": "normal_run" if use_experience else "retrieval_control_baseline",
            }
            self._emit_progress(progress_callback, "spec", "Loading spec context.")
            logger.set_spec_context(build_spec_context(self.config.paths.root, spec_files))
            self._emit_progress(progress_callback, "retrieve", "Retrieving project experience.")
            retrieved = self.store.retrieve(intent, limit=8) if use_experience else []
            logger.set_loaded_context(retrieved, injected_paths=set())
            activation_assets = [
                {
                    **item,
                    "model_role": "coding_agent",
                    "executor_id": self._primary_executor().get("executor_id"),
                }
                for item in retrieved
            ]
            self.store.record_asset_usage(logger.task_id, activation_assets, used_in_prompt=False)
            self._emit_progress(progress_callback, "analyze", "Analyzing task risk and intent.")
            analysis_rules = [item for item in retrieved if item.get("kind") == "rule"]
            if task_input:
                analysis = self.analyzer.analyze(intent, analysis_rules,
                    environment_instructions=task_input.environment_instructions)
            else:
                analysis = self.analyzer.analyze(task, analysis_rules)
            logger.set_task_analysis(analysis)
            activation_observations = self._activation_observations(snapshot)
            logger.data["activation_observations"] = activation_observations
            prompt_context, representation_decisions = RuntimeExperienceContext(self.config, self.store).plan(
                task, logger.task_id, analysis, retrieved, observations=activation_observations
            )
            logger.data["experience_representation"] = {
                "schema_version": "praxile.runtime_experience_representation.v1",
                "decisions": representation_decisions,
                "selected_count": len(prompt_context),
            }
            by_path = {item["path"]: item for item in representation_decisions}
            for item in logger.data["loaded_assets"]:
                decision = by_path.get(item.get("path"))
                if decision:
                    item["representation_kind"] = decision["representation"]
                    item["activation_status"] = decision["activation"]
                    item["experience_layer"] = decision.get("experience_layer")
            self._emit_progress(progress_callback, "plan", "Recording initial plan.", {"plan": analysis.get("plan")})
            logger.set_plan(analysis["plan"])
            if self._parallel_readonly_enabled(parallel_readonly_explore):
                self._emit_progress(progress_callback, "explore", "Running parallel read-only exploration.")
                self._run_parallel_readonly_exploration(task, logger)
            self._emit_progress(progress_callback, "route", "Selecting model route.")
            route = self.router.describe_route(
                "coding_agent",
                private=analysis["privacy_sensitive"],
                high_risk=analysis["high_risk"],
            )
            logger.set_model_route(route)
            self._trace(
                "task_started",
                task_id=logger.task_id,
                task=task,
                route=route,
                analysis=analysis,
            )
            self._write_checkpoint(
                logger,
                messages=[],
                context={
                    "task": task,
                    "retrieved": prompt_context,
                    "private": analysis["privacy_sensitive"],
                    "high_risk": analysis["high_risk"],
                    "dry_run": dry_run,
                    "test_commands": test_commands,
                    "spec_files": spec_files or [],
                    "parallel_readonly_explore": parallel_readonly_explore,
                },
            )

            if analysis["architecture_gate_required"]:
                self._emit_progress(progress_callback, "architecture_gate", "Architecture gate triggered; implementation is paused.")
                gate_summary = self._architecture_gate_summary(task, analysis, retrieved)
                logger.add_action(
                    action_type="architecture_gate",
                    input_data={"task": task},
                    observation={
                        "status": "needs_human",
                        "output": gate_summary,
                        "data": {
                            **analysis,
                            "implementation_blocked": True,
                            "blocked_action_types": ["edit_file", "create_docx", "revise_docx", "run_command"],
                            "resume_policy": "Accept or edit the architecture-gate proposal, then start an explicit implementation task.",
                        },
                        "risk_level": "medium",
                    },
                    status="needs_human",
                    executor=self._primary_executor(),
                )
                result_status = "needs_human"
                result_summary = "Architecture gate triggered; no code edits were performed."
                if self.config.get("architecture_gate", "shadow_mode", default=False):
                    logger.data["architecture_gate_shadow_mode"] = True
                    self._run_action_loop(
                        task,
                        logger,
                        prompt_context,
                        max_steps=max_steps or int(self.config.get("runtime", "max_steps", default=10)),
                        private=analysis["privacy_sensitive"],
                        high_risk=True,
                        dry_run=True,
                        checkpoint_context={
                            "task": task,
                            "retrieved": prompt_context,
                            "private": analysis["privacy_sensitive"],
                            "high_risk": True,
                            "dry_run": True,
                            "test_commands": test_commands,
                            "spec_files": spec_files or [],
                            "parallel_readonly_explore": parallel_readonly_explore,
                        },
                        cancel_requested=cancel_requested,
                        progress_callback=progress_callback,
                    )
                    result_summary = "Architecture gate triggered; shadow-mode planning was recorded without landing edits."
            else:
                result_status, result_summary = self._run_action_loop(
                    task,
                    logger,
                    prompt_context,
                    max_steps=max_steps or int(self.config.get("runtime", "max_steps", default=10)),
                    private=analysis["privacy_sensitive"],
                    high_risk=analysis["high_risk"],
                    dry_run=dry_run,
                    checkpoint_context={
                        "task": task,
                        "retrieved": prompt_context,
                        "private": analysis["privacy_sensitive"],
                        "high_risk": analysis["high_risk"],
                        "dry_run": dry_run,
                        "test_commands": test_commands,
                        "spec_files": spec_files or [],
                        "parallel_readonly_explore": parallel_readonly_explore,
                    },
                    cancel_requested=cancel_requested,
                    progress_callback=progress_callback,
                )

            return self._finish_run(
                logger,
                result_status,
                result_summary,
                test_commands=test_commands,
                dry_run=dry_run,
                cancel_requested=cancel_requested,
                progress_callback=progress_callback,
            )
        finally:
            self.tools.close()

    def _activation_observations(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        state = dict(snapshot)
        versions = observe_tool_versions()
        browser = getattr(self.tools, "browser", None)
        if browser is not None:
            observed = browser.observe_state()
            state["browser"] = {key: value for key, value in observed.items() if key != "chromium_version"}
            if "chromium_version" in observed:
                versions["chromium"] = observed["chromium_version"]
        return {"state": state, "tool_versions": versions}

    def _resume_run(
        self,
        task_id: str,
        *,
        task_override: str | None,
        test_commands: list[str] | None,
        max_steps: int | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        checkpoint = self.store.load_checkpoint(task_id)
        if not checkpoint:
            raise FileNotFoundError(f"No checkpoint found for task: {task_id}")
        if "pending_action" in checkpoint["trajectory"]:
            raise ValueError("checkpoint contains an unconfirmed action; inspect tool side effects before starting a new run")
        logger = TrajectoryLogger.from_data(checkpoint["trajectory"])
        frozen_contract = logger.data.get("deliverable_contract")
        requested_contract = self.tools.deliverable_contract
        if requested_contract is not None and json.dumps(requested_contract, sort_keys=True) != json.dumps(frozen_contract, sort_keys=True):
            raise ValueError("cannot change the deliverable contract on resume; start a new run")
        self.tools.bind_deliverable_contract(frozen_contract)
        self._register_base_executors(logger)
        context = dict(checkpoint.get("context") or {})
        original_task = str(context.get("task") or logger.data.get("user_task") or "")
        if task_override and task_override != original_task and frozen_contract is not None:
            raise ValueError("cannot change task text while resuming a bound deliverable contract")
        if task_override and task_override != original_task and logger.data.get("experience_representation"):
            raise ValueError("cannot change task text while resuming a frozen experience representation; start a new run")
        task = task_override or context.get("task") or logger.data.get("user_task", "")
        if task_override:
            logger.data["user_task"] = task_override
        resume_dry_run = dry_run or bool(context.get("dry_run", False))
        resume_test_commands = test_commands if test_commands is not None else context.get("test_commands")
        resume_spec_files = context.get("spec_files") or []
        if not logger.data.get("spec_context"):
            logger.set_spec_context(build_spec_context(self.config.paths.root, resume_spec_files))
        messages = checkpoint.get("messages") or None
        retrieved = context.get("retrieved") or []
        if any(item.get("activation_requirements") is not None for item in retrieved):
            snapshot = self.project.snapshot(refresh=True)
            snapshot["interop"] = interop_policy(self.config)
            observations = self._activation_observations(snapshot)
            checks = [{"path": item.get("path"), **check_conditions(
                item.get("activation_requirements"), observations)} for item in retrieved]
            rejected = any(check["status"] == "abstained" for check in checks)
            self._trace("resume_experience_conditions", task_id=logger.task_id,
                        rejected=rejected, checks=checks)
            if rejected:
                raise ValueError("frozen experience conditions no longer hold; start a new run instead of resuming stale context")
            logger.data.setdefault("activation_revalidations", []).append({
                "at": utc_now(), "phase": "resume", "observations": observations, "checks": checks})
        private = bool(context.get("private", logger.data.get("task_analysis", {}).get("privacy_sensitive", False)))
        high_risk = bool(context.get("high_risk", logger.data.get("task_analysis", {}).get("high_risk", False)))
        logger.data.setdefault("resume_events", []).append({"resumed_at": utc_now(), "checkpoint": checkpoint.get("path")})
        self._trace("task_resumed", task_id=logger.task_id, task=task, actions=len(logger.data.get("actions", [])))
        result_status, result_summary = self._run_action_loop(
            task,
            logger,
            retrieved,
            max_steps=max_steps or int(self.config.get("runtime", "max_steps", default=10)),
            private=private,
            high_risk=high_risk,
            dry_run=resume_dry_run,
            messages=messages,
            checkpoint_context={
                **context,
                "task": task,
                "retrieved": retrieved,
                "private": private,
                "high_risk": high_risk,
                "dry_run": resume_dry_run,
                "test_commands": resume_test_commands,
                "spec_files": resume_spec_files,
            },
        )
        return self._finish_run(
            logger,
            result_status,
            result_summary,
            test_commands=resume_test_commands,
            dry_run=resume_dry_run,
        )

    def _finish_run(
        self,
        logger: TrajectoryLogger,
        result_status: str,
        result_summary: str,
        *,
        test_commands: list[str] | None,
        dry_run: bool,
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        cancelled = "cancelled" in str(result_summary or "").lower()
        self._emit_progress(progress_callback, "verify", "Running verification commands." if test_commands and not dry_run and not cancelled else "Preparing verification summary.")
        test_results = [] if dry_run or cancelled else self.tests.run(test_commands, cancel_requested=cancel_requested) if test_commands else []
        if cancelled and test_commands:
            logger.add_action(
                action_type="cancelled_skip_tests",
                input_data={"commands": test_commands},
                observation={
                    "status": "needs_human",
                    "output": "Cancelled run skipped verification commands.",
                    "data": {"cancelled": True, "commands": test_commands},
                    "risk_level": "low",
                },
                status="needs_human",
                executor=self._verification_executor(),
            )
        if dry_run and test_commands:
            logger.add_action(
                action_type="dry_run_skip_tests",
                input_data={"commands": test_commands},
                observation={
                    "status": "success",
                    "output": "Dry-run mode skipped verification commands.",
                    "data": {"dry_run": True, "commands": test_commands},
                    "risk_level": "low",
                },
                status="success",
                executor=self._verification_executor(),
            )
        for result in test_results:
            logger.add_action(
                action_type="run_test",
                input_data={"command": result.get("data", {}).get("command")},
                observation=result,
                status=result.get("status", "unknown"),
                executor=self._verification_executor(),
            )

        self._emit_progress(progress_callback, "diff", "Collecting git diff summary.")
        if self.tools.deliverable_contract is not None and (not cancelled or result_status == "completed"):
            observation = self.tools.execute({"type": "check_deliverables", "contract": self.tools.deliverable_contract},
                task_id=logger.task_id, step=len(logger.data["actions"]) + 1)
            logger.data["deliverable_verification"] = observation
            logger.add_action(action_type="deliverable_verification", input_data={"source": "run_argument"},
                observation=observation, status=observation["status"], executor=self._verification_executor())
            if observation["status"] != "success" and result_status == "completed":
                result_status = "needs_human"
                result_summary += " Caller-bound deliverable requirements were not verified."
        logger.set_diff_summary(self.git.diff_summary())
        trajectory = logger.finish(status=result_status, summary=result_summary)
        if (trajectory.get("spec_context") or {}).get("spec_files"):
            self._emit_progress(progress_callback, "spec_verify", "Verifying run against spec context.")
            trajectory["spec_compliance"] = verify_spec_compliance(self.config.paths.root, trajectory)
        self._emit_progress(progress_callback, "silent_failure", "Checking silent failure signals.")
        trajectory["silent_failure_signals"] = detect_silent_failure_signals(trajectory, test_results)
        self._emit_progress(progress_callback, "reward", "Building reward report.")
        llm_judge = self._llm_judge_reward(trajectory)
        if llm_judge:
            trajectory["llm_judge_reward"] = llm_judge
        report = self.reward.build_report(trajectory, test_results)
        trajectory["reward_report"] = report
        judge_observation = self._record_judge_governance(trajectory)
        for field in (
            "self_judgment",
            "verifier_outcome",
            "next_task_delta",
            "judgment_calibration",
            "transfer_effect",
        ):
            trajectory[field] = judge_observation[field]
        self._emit_progress(progress_callback, "evolve", "Generating experience proposals.")
        proposals = self.evolution.generate(trajectory)
        trajectory["experience_candidates"] = [
            {
                "proposal_id": proposal["proposal_id"],
                "type": proposal["type"],
                "title": proposal["title"],
                "risk_level": proposal["risk_level"],
                "priority": proposal.get("priority"),
                "confidence": proposal.get("confidence"),
                "confidence_level": proposal.get("confidence_level"),
                "evidence_summary": proposal.get("evidence_summary"),
                "proposal_gate": proposal.get("proposal_gate"),
                "target_files": proposal["target_files"],
            }
            for proposal in proposals
        ]
        trajectory["evolution_summary"] = self._evolution_summary(trajectory, proposals)
        referenced_paths = self._referenced_asset_paths(trajectory)
        self._emit_progress(progress_callback, "attribution", "Judging loaded experience attribution.")
        attribution_results = AttributionJudge(self.config, self.router).judge_loaded_assets(
            trajectory,
            self._usage_outcome(trajectory),
            referenced_paths,
        )
        if attribution_results:
            trajectory["semantic_attributions"] = attribution_results
            self._attach_semantic_attributions(trajectory, attribution_results)
        self._emit_progress(progress_callback, "persist", "Persisting trajectory and proposals.")
        self.store.update_asset_usage_outcome(
            logger.task_id,
            self._usage_outcome(trajectory),
            referenced_paths=referenced_paths,
            attribution_results=attribution_results,
        )
        trajectory["experience_activation"] = self.store.activation_funnel_for_task(logger.task_id)
        from .bounded_evolution import BoundedHarnessEvolution
        rollback_events = BoundedHarnessEvolution(self.config, self.store).monitor(trajectory)
        if rollback_events:
            trajectory["harness_rollback_events"] = rollback_events
        self.store.record_trajectory(trajectory)
        self.store.record_judge_observation(judge_observation)
        for proposal in proposals:
            self.store.write_proposal(proposal)
        self.store.delete_checkpoint(logger.task_id)
        self._trace("task_finished", task_id=logger.task_id, status=result_status, proposals=len(proposals))
        self._emit_progress(
            progress_callback,
            "finished",
            "Run finished.",
            {"task_id": logger.task_id, "status": result_status, "proposals": len(proposals)},
        )
        return trajectory

    def _record_judge_governance(self, trajectory: dict[str, Any]) -> dict[str, Any]:
        governance = JudgeGovernance(self.config)
        current = governance.observe(trajectory)
        source_assets: dict[str, list[str]] = {}
        for asset in trajectory.get("loaded_assets") or []:
            if not asset.get("used_in_prompt", True):
                continue
            source_task_id = str(asset.get("source_task_id") or "").strip()
            path = str(asset.get("path") or "").strip()
            if source_task_id and source_task_id != trajectory.get("task_id") and path:
                source_assets.setdefault(source_task_id, []).append(path)
        for source_task_id, paths in source_assets.items():
            source = self.store.get_judge_observation(source_task_id)
            if not source:
                continue
            transfer = governance.transfer_observation(
                source,
                current,
                current_task_id=str(trajectory.get("task_id") or ""),
                asset_paths=paths,
            )
            if transfer:
                self.store.record_judge_observation(governance.apply_transfer(source, transfer))
        previous = self.store.list_judge_observations(limit=1000)
        current["aggregate_metrics_before_current_run"] = JudgeGovernance.metrics(previous)
        current["aggregate_metrics_including_current_run"] = JudgeGovernance.metrics([*previous, current])
        return current

    def _llm_judge_reward(self, trajectory: dict[str, Any]) -> dict[str, Any] | None:
        enabled = bool(
            self.config.get(
                "reward",
                "llm_judge",
                "enabled",
                default=self.config.get("reward", "llm_judge_enabled", default=False),
            )
        )
        if not enabled:
            return None
        role = str(self.config.get("reward", "llm_judge", "role", default="reward_judge"))
        compact = {
            "task_id": trajectory.get("task_id"),
            "user_task": trajectory.get("user_task"),
            "task_analysis": trajectory.get("task_analysis"),
            "result": trajectory.get("result"),
            "diff_summary": {
                "files_changed": trajectory.get("diff_summary", {}).get("files_changed"),
                "insertions": trajectory.get("diff_summary", {}).get("insertions"),
                "deletions": trajectory.get("diff_summary", {}).get("deletions"),
            },
            "actions": [
                {
                    "step": action.get("step"),
                    "type": action.get("action_type"),
                    "status": action.get("status"),
                    "output": shorten(str(action.get("observation", {}).get("output", "")), 500),
                }
                for action in trajectory.get("actions", [])[:16]
            ],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Praxile's optional reward judge. Return exactly one JSON object with numeric scores "
                    "between 0 and 1. Do not replace objective test/safety signals; judge only quality, specificity, "
                    "scope control, evidence quality, and overgeneralization risk."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Return schema: {\"specificity\":0.0,\"scope_control\":0.0,"
                    "\"evidence_quality\":0.0,\"intent_alignment\":0.0,"
                    "\"overgeneralization_risk\":0.0,\"recommended_action\":\"inspect\","
                    "\"score\":0.0,\"reasons\":[\"short reason\"]}\n\n"
                    f"Trajectory:\n{json.dumps(compact, ensure_ascii=False)}"
                ),
            },
        ]
        try:
            response = self.router.chat(
                messages,
                purpose=role,
                private=bool(trajectory.get("task_analysis", {}).get("privacy_sensitive")),
                high_risk=bool(trajectory.get("task_analysis", {}).get("high_risk")),
                temperature=0,
                max_tokens=int(self.config.get("reward", "llm_judge", "max_tokens", default=800)),
                timeout=int(self.config.get("reward", "llm_judge", "timeout_seconds", default=12)),
            )
            payload = parse_json_object(response.get("content", ""))
            usage = response.get("usage", {})
            route = response.get("route", {})
            self._trace(
                "reward_judge_response",
                task_id=trajectory.get("task_id"),
                route=route,
                latency_ms=response.get("latency_ms"),
                usage=usage,
            )
            specificity = _score(payload.get("specificity"), default=0.5)
            scope_control = _score(payload.get("scope_control"), default=0.5)
            evidence_quality = _score(payload.get("evidence_quality"), default=0.5)
            intent_alignment = _score(payload.get("intent_alignment"), default=0.5)
            overgeneralization_risk = _score(payload.get("overgeneralization_risk"), default=0.5)
            computed_score = round(
                (specificity + scope_control + evidence_quality + intent_alignment + (1.0 - overgeneralization_risk)) / 5,
                3,
            )
            recommended_action = str(payload.get("recommended_action") or "inspect")
            if recommended_action not in {"accept", "inspect", "reject_or_edit"}:
                recommended_action = "inspect"
            if overgeneralization_risk >= 0.65 and recommended_action == "accept":
                recommended_action = "inspect"
            reasons_raw = payload.get("reasons", payload.get("notes", []))
            reasons = [str(item) for item in reasons_raw if str(item).strip()][:5] if isinstance(reasons_raw, list) else []
            return {
                "enabled": True,
                "active": True,
                "model_role": role,
                "provider": response.get("provider"),
                "model": response.get("model"),
                "prompt_version": "reward-judge-v1",
                "route": route,
                "latency_ms": response.get("latency_ms"),
                "specificity": specificity,
                "scope_control": scope_control,
                "evidence_quality": evidence_quality,
                "intent_alignment": intent_alignment,
                "overgeneralization_risk": overgeneralization_risk,
                "recommended_action": recommended_action,
                "score": _score(payload.get("score"), default=computed_score),
                "reasons": reasons,
                "notes": reasons,
                "usage": usage,
            }
        except Exception as exc:
            self._trace("reward_judge_unavailable", task_id=trajectory.get("task_id"), model_role=role, error=str(exc))
            return {
                "enabled": True,
                "active": False,
                "model_role": role,
                "score": 0.0,
                "error": f"{exc.__class__.__name__}: {exc}",
                "notes": ["Optional LLM judge was unavailable; final reward used active non-LLM components."],
            }

    def _evolution_summary(self, trajectory: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
        loaded_assets = trajectory.get("loaded_assets") or []
        used_assets = [item for item in loaded_assets if item.get("used_in_prompt", True)]
        loaded_counts: dict[str, int] = {}
        for item in used_assets:
            key = str(item.get("kind") or item.get("asset_type") or "asset")
            loaded_counts[key] = loaded_counts.get(key, 0) + 1
        proposal_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        confidence_counts: dict[str, int] = {}
        for proposal in proposals:
            proposal_counts[proposal["type"]] = proposal_counts.get(proposal["type"], 0) + 1
            risk = proposal.get("risk_level", "low")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
            level = proposal.get("confidence_level", "unknown")
            confidence_counts[level] = confidence_counts.get(level, 0) + 1
        return {
            "used_assets": len(used_assets),
            "retrieved_assets": len(loaded_assets),
            "used_asset_counts": loaded_counts,
            "produced_proposals": len(proposals),
            "proposal_counts": proposal_counts,
            "proposal_risk_counts": risk_counts,
            "proposal_confidence_counts": confidence_counts,
            "proposal_gate": trajectory.get("proposal_gate_summary", {}),
            "silent_failure_signals": trajectory.get("silent_failure_signals", []),
            "experience_generation": trajectory.get("reward_report", {}).get("experience_generation", {}),
            "review_command": f"praxile review --source-run {trajectory.get('task_id')}",
        }

    def _usage_outcome(self, trajectory: dict[str, Any]) -> str:
        status = trajectory.get("result", {}).get("status")
        if status == "completed" and trajectory.get("reward_report", {}).get("regression_passed") is not False:
            return "success"
        if status == "failed" or trajectory.get("reward_report", {}).get("regression_passed") is False:
            return "failed"
        if status == "needs_human":
            return "needs_human"
        return "unknown"

    def _register_base_executors(self, logger: TrajectoryLogger) -> None:
        logger.register_executor(
            self.primary_executor_id,
            kind="agent_runtime",
            role="coding_agent",
            description="Primary Praxile coding/runtime executor.",
        )
        logger.register_executor(
            "verification",
            kind="verification",
            role="test_runner",
            description="Runs configured test/lint/build verification commands.",
        )

    def _primary_executor(self) -> dict[str, Any]:
        return {
            "executor_id": self.primary_executor_id,
            "kind": "agent_runtime",
            "role": "coding_agent",
        }

    def _verification_executor(self) -> dict[str, Any]:
        return {
            "executor_id": "verification",
            "kind": "verification",
            "role": "test_runner",
        }

    def _action_executor(self, action_type: str | None, observation: dict[str, Any]) -> dict[str, Any]:
        if action_type == "batch":
            executor = (observation.get("data") or {}).get("executor")
            if isinstance(executor, dict):
                return executor
        return self._primary_executor()

    def _parallel_readonly_enabled(self, override: bool | None) -> bool:
        if override is not None:
            return bool(override)
        return bool(self.config.get("executors", "parallel_readonly_exploration_enabled", default=False))

    def _run_parallel_readonly_exploration(self, task: str, logger: TrajectoryLogger) -> None:
        actions = self._readonly_exploration_actions(task)
        if not actions:
            return
        coordinator = {
            "executor_id": "parallel_readonly",
            "kind": "parallel_readonly_coordinator",
            "role": "pre_model_exploration",
        }
        logger.register_executor(
            coordinator["executor_id"],
            kind=coordinator["kind"],
            role=coordinator["role"],
            description="Coordinates safe concurrent read-only project exploration before model action planning.",
            parent_executor_id=self.primary_executor_id,
        )
        observation = self.tools.execute(
            {
                "type": "batch",
                "executor_id": coordinator["executor_id"],
                "actions": actions,
            },
            task_id=logger.task_id,
            step=len(logger.data.get("actions", [])) + 1,
        )
        for executor in (observation.get("data") or {}).get("executor_events") or []:
            if isinstance(executor, dict):
                logger.register_executor(
                    str(executor.get("executor_id") or ""),
                    kind=str(executor.get("kind") or "readonly_worker"),
                    role=str(executor.get("role") or "read_only"),
                    parent_executor_id=str(executor.get("parent_executor_id") or coordinator["executor_id"]),
                    description="Concurrent read-only exploration worker.",
                )
        logger.data["parallel_readonly_exploration"] = {
            "enabled": True,
            "action_count": len(actions),
            "status": observation.get("status"),
            "created_at": utc_now(),
        }
        logger.add_action(
            action_type="parallel_readonly_exploration",
            input_data={"actions": actions},
            observation=observation,
            status=observation.get("status", "unknown"),
            executor=coordinator,
        )
        self._trace(
            "parallel_readonly_exploration",
            task_id=logger.task_id,
            status=observation.get("status"),
            actions=len(actions),
            executors=len((observation.get("data") or {}).get("executor_events") or []),
        )

    def _readonly_exploration_actions(self, task: str) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {"type": "project_map", "refresh": False},
            {"type": "list_files"},
        ]
        query = _exploration_query(task)
        if query:
            actions.append({"type": "find_files", "query": query, "limit": 40})
            actions.append({"type": "search", "pattern": query, "limit": 40})
        max_concurrency = max(1, min(16, int(self.config.get("executors", "max_readonly_concurrency", default=8) or 8)))
        return actions[:max_concurrency]

    def _referenced_asset_paths(self, trajectory: dict[str, Any]) -> list[str]:
        loaded = trajectory.get("loaded_assets") or []
        if not loaded:
            return []
        text_parts = [
            str(trajectory.get("result", {}).get("summary") or ""),
            json.dumps(trajectory.get("experience_candidates") or [], ensure_ascii=False),
        ]
        for action in trajectory.get("actions", []):
            text_parts.append(json.dumps(action.get("input") or {}, ensure_ascii=False))
            text_parts.append(str(action.get("observation", {}).get("output") or ""))
        haystack = "\n".join(text_parts)
        referenced: list[str] = []
        for item in loaded:
            if not item.get("used_in_prompt", True):
                continue
            path = str(item.get("path") or item.get("asset_id") or "")
            if not path:
                continue
            basename = path.rsplit("/", 1)[-1]
            stem = basename.rsplit(".", 1)[0]
            if path in haystack or (stem and len(stem) > 3 and stem in haystack):
                referenced.append(path)
        return list(dict.fromkeys(referenced))

    def _attach_semantic_attributions(self, trajectory: dict[str, Any], attributions: list[dict[str, Any]]) -> None:
        by_path = {str(item.get("path") or ""): item for item in attributions if isinstance(item, dict)}
        for asset in trajectory.get("loaded_assets") or []:
            path = str(asset.get("path") or asset.get("asset_id") or "")
            if path in by_path:
                asset["semantic_attribution"] = by_path[path]
                asset["attribution_level"] = by_path[path].get("attribution_level")

    def _architecture_gate_summary(self, task: str, analysis: dict[str, Any], retrieved: list[dict[str, Any]]) -> str:
        boundaries = "\n".join(f"- {item['path']}" for item in analysis.get("frozen_hits", [])) or "- none"
        return (
            "Architecture Gate\n\n"
            f"Task: {task}\n\n"
            "Why local patch is insufficient: the task appears to touch architecture-sensitive terms and matching "
            "frozen-boundary rules were retrieved.\n\n"
            f"Retrieved boundaries:\n{boundaries}\n\n"
            "Required before edits: define goal, affected modules/contracts, alternatives, minimal migration path, "
            "rollback plan, and verification plan."
            "\n\nImplementation is blocked: Praxile must not edit files or run implementation commands for this task "
            "until the architecture-gate proposal is reviewed and the user starts an explicit implementation task."
        )

    def _run_action_loop(
        self,
        task: str,
        logger: TrajectoryLogger,
        retrieved: list[dict[str, Any]],
        *,
        max_steps: int,
        private: bool,
        high_risk: bool,
        dry_run: bool = False,
        messages: list[dict[str, str]] | None = None,
        checkpoint_context: dict[str, Any] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, str]:
        messages = messages or self._initial_messages(task, logger, retrieved)
        invalid_action_count = 0
        parse_retry_limit = int(self.config.get("runtime", "action_parse_retries", default=2))
        invalid_action_fail_fast_count = max(
            1,
            int(self.config.get("runtime", "invalid_action_fail_fast_count", default=parse_retry_limit + 1) or parse_retry_limit + 1),
        )
        try:
            for _ in range(max_steps):
                if cancel_requested and cancel_requested():
                    self._emit_progress(progress_callback, "cancelled", "Cancellation requested before the next runtime step.")
                    logger.add_action(
                        action_type="gateway_cancelled",
                        input_data={"source": "web_console"},
                        observation={
                            "status": "needs_human",
                            "output": "Run cancelled by Web Console stop request before the next model/tool step.",
                            "data": {"cancelled": True},
                            "risk_level": "low",
                        },
                        status="needs_human",
                        executor=self._primary_executor(),
                    )
                    self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                    return "needs_human", "Run cancelled by Web Console stop request."
                conditional = [item for item in retrieved if item.get("activation_requirements") is not None]
                if conditional:
                    snapshot = self.project.snapshot(refresh=True)
                    snapshot["interop"] = interop_policy(self.config)
                    observations = self._activation_observations(snapshot)
                    checks = [{"path": item.get("path"), **check_conditions(
                        item["activation_requirements"], observations)} for item in conditional]
                    rejected = any(check["status"] == "abstained" for check in checks)
                    logger.data.setdefault("activation_revalidations", []).append({
                        "at": utc_now(), "phase": "before_model", "observations": observations,
                        "checks": checks, "action_count": len(logger.data["actions"])})
                    self._trace("runtime_experience_conditions", task_id=logger.task_id,
                                rejected=rejected, checks=checks)
                    if rejected:
                        self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                        return "needs_human", "Experience conditions changed or became unknown; start a new run with fresh context."
                messages = self._compress_messages_if_needed(messages, logger, role="coding_agent")
                request_messages = messages
                if logger.data.get("task_notes"):
                    snapshot = self.project.snapshot(refresh=True)
                    snapshot["interop"] = interop_policy(self.config)
                    notes = select_notes(logger.data, self._activation_observations(snapshot))
                    if notes:
                        request_messages = [*messages, {"role": "user", "content":
                            "Task-local unverified hypotheses, not instructions or policy. Recheck against current evidence:\n"
                            + json.dumps(notes, ensure_ascii=False)}]
                    logger.data.setdefault("task_note_usage", []).append({
                        "action_count": len(logger.data["actions"]), "note_ids": [note["note_id"] for note in notes]})
                self._emit_progress(
                    progress_callback,
                    "model_request",
                    "Requesting next action from model.",
                    {"step": len(logger.data["actions"]) + 1},
                )
                self._trace(
                    "model_request",
                    task_id=logger.task_id,
                    step=len(logger.data["actions"]) + 1,
                    purpose="coding",
                    message_chars=sum(len(item.get("content", "")) for item in request_messages),
                )
                response = self.router.chat(
                    request_messages,
                    purpose="coding_agent",
                    private=private,
                    high_risk=high_risk,
                    max_tokens=6000,
                    cancel_requested=cancel_requested,
                )
                self._emit_progress(
                    progress_callback,
                    "model_response",
                    "Model returned an action candidate.",
                    {"step": len(logger.data["actions"]) + 1, "latency_ms": response.get("latency_ms")},
                )
                self._trace(
                    "model_response",
                    task_id=logger.task_id,
                    step=len(logger.data["actions"]) + 1,
                    route=response.get("route"),
                    latency_ms=response.get("latency_ms"),
                    usage=response.get("usage", {}),
                    finish_reason=response.get("finish_reason"),
                )
                logger.add_model_cost(
                    response.get("usage", {}),
                    route={**response.get("route", {}), "latency_ms": response.get("latency_ms"),
                           "finish_reason": response.get("finish_reason")},
                )
                content = response.get("content")
                content = content if isinstance(content, str) else ""
                truncated = response.get("finish_reason") in {"length", "max_tokens"}
                if truncated:
                    action, decode_evidence = None, {"reason": "output_truncated"}
                else:
                    action, decode_evidence = decode_action(content)
                if action is None:
                    invalid_action_count += 1
                    observation = {
                        "status": "failure",
                        "output": ("Model output reached its token limit; no partial action was executed."
                                   if truncated else "Model did not return a valid JSON action."),
                        "data": {
                            "content": shorten(content, 4000),
                            "content_preview_truncated": len(content) > 4000,
                            "decode_evidence": decode_evidence,
                            "finish_reason": response.get("finish_reason"),
                            "failure_kind": "output_truncated" if truncated else "invalid_json_action",
                            "parse_retry": invalid_action_count,
                            "parse_retry_limit": parse_retry_limit,
                            "invalid_action_fail_fast_count": invalid_action_fail_fast_count,
                        },
                        "risk_level": "low",
                    }
                    logger.add_action(
                        action_type="model_response",
                        input_data={},
                        observation=observation,
                        status="failure",
                        executor=self._primary_executor(),
                    )
                    logger.add_model_performance(
                        {
                            "provider": response.get("provider"),
                            "model": response.get("model"),
                            "purpose": "coding",
                            "status": "invalid_action",
                            "failure_pattern": "model_output_truncated" if truncated else "model_response_not_json_action",
                        }
                    )
                    if invalid_action_count >= invalid_action_fail_fast_count:
                        return (
                            "needs_human",
                            f"Stopped after {invalid_action_count} consecutive invalid JSON action response(s); review model output.",
                        )
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": (
                        "Your response hit the output token limit and was not executed. Return a shorter complete action; "
                        "do not continue a JSON suffix and do not assume the previous action happened. "
                        if truncated else
                        "Your response was rejected and NO action was executed. Do not assume any file changed. "
                        "Decoder diagnostics: " + json.dumps(decode_evidence) + ". "
                        ) + "Return only the final JSON action object; no prose, markdown, or <think> blocks."})
                    self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                    continue

                schema_valid, schema_errors = self.action_schemas.validate(action)
                if not schema_valid:
                    invalid_action_count += 1
                    observation = {
                        "status": "failure",
                        "output": "Model returned JSON that failed the Praxile Action Schema.",
                        "data": {
                            "errors": schema_errors,
                            "content": shorten(content, 4000),
                            "parse_retry": invalid_action_count,
                            "parse_retry_limit": parse_retry_limit,
                            "invalid_action_fail_fast_count": invalid_action_fail_fast_count,
                        },
                        "risk_level": "low",
                    }
                    logger.add_action(
                        action_type="model_response",
                        input_data={},
                        observation=observation,
                        status="failure",
                        executor=self._primary_executor(),
                    )
                    logger.add_model_performance(
                        {
                            "provider": response.get("provider"),
                            "model": response.get("model"),
                            "purpose": "coding",
                            "status": "invalid_action_schema",
                            "failure_pattern": "model_action_schema_invalid",
                        }
                    )
                    if invalid_action_count >= invalid_action_fail_fast_count:
                        return (
                            "needs_human",
                            f"Stopped after {invalid_action_count} consecutive invalid action schema response(s); review model output.",
                        )
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": self.action_schemas.repair_prompt(schema_errors)})
                    self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                    continue

                action_type = action.get("type")
                invalid_action_count = 0
                if cancel_requested and cancel_requested():
                    self._emit_progress(progress_callback, "cancelled", "Cancellation requested before executing the pending action.", {"pending_action": action_type})
                    logger.add_action(
                        action_type="gateway_cancelled",
                        input_data={"source": "web_console", "pending_action": action_type},
                        observation={
                            "status": "needs_human",
                            "output": "Run cancelled by Web Console stop request before executing the pending action.",
                            "data": {"cancelled": True, "pending_action": action},
                            "risk_level": "low",
                        },
                        status="needs_human",
                        executor=self._primary_executor(),
                    )
                    self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                    return "needs_human", "Run cancelled by Web Console stop request."
                # Persist intent before dispatch; a crash cannot turn an unknown
                # tool outcome into a silently replayable checkpoint.
                logger.data["pending_action"] = {
                    "schema_version": 1,
                    "step": len(logger.data["actions"]) + 1,
                    "action_type": action_type,
                    "action_sha256": hashlib.sha256(json.dumps(
                        action, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
                    "status": "outcome_unconfirmed",
                }
                self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {}, force=True)
                if action_type == "subtask_reflect":
                    decision = self.tools.safety.check_tool_call(action_type, action,
                        context={"task_id": logger.task_id, "step": len(logger.data["actions"]) + 1})
                    if not decision.allowed:
                        observation = {"status": "blocked", "output": decision.reason, "data": {}}
                    else:
                        snapshot = self.project.snapshot(refresh=True)
                        snapshot["interop"] = interop_policy(self.config)
                        try:
                            observation = reflect_subtask(logger, action, self._activation_observations(snapshot),
                                self.router, private=private, high_risk=high_risk, cancel_requested=cancel_requested)
                        except (ValueError, RobustJSONError) as exc:
                            observation = {"status": "failure", "output": "Reflection rejected: " + type(exc).__name__, "data": {}}
                elif action_type in {"subtask_begin", "subtask_end", "subtask_note"}:
                    observation = self._subtask_boundary(action, logger)
                elif dry_run and action_type in MUTATING_ACTIONS:
                    observation = {
                        "status": "blocked",
                        "output": f"Dry-run mode blocked `{action_type}`.",
                        "data": {"dry_run": True, "blocked_action": action_type},
                        "risk_level": "low",
                    }
                else:
                    self._emit_progress(
                        progress_callback,
                        "tool_start",
                        f"Executing tool action `{action_type}`.",
                        {"step": len(logger.data["actions"]) + 1, "action_type": action_type},
                    )
                    observation = self.tools.execute(action, task_id=logger.task_id, step=len(logger.data["actions"]) + 1)
                logger.add_action(
                    action_type=action_type or "unknown",
                    input_data={k: v for k, v in action.items() if k != "content"},
                    observation=observation,
                    status=observation.get("status", "unknown"),
                    executor=self._action_executor(action_type, observation),
                )
                self._trace(
                    "tool_action",
                    task_id=logger.task_id,
                    step=len(logger.data["actions"]),
                    action_type=action_type,
                    status=observation.get("status"),
                    risk_level=observation.get("risk_level"),
                    output=shorten(observation.get("output", ""), 1000),
                )
                self._emit_progress(
                    progress_callback,
                    "tool_finish",
                    f"Tool action `{action_type}` finished with {observation.get('status')}.",
                    {
                        "step": len(logger.data["actions"]),
                        "action_type": action_type,
                        "status": observation.get("status"),
                        "risk_level": observation.get("risk_level"),
                    },
                )
                self._refresh_snapshot_after_action(logger, action_type, observation)
                artifact = observation.get("data", {}).get("artifact")
                if artifact:
                    logger.add_artifact(
                        {
                            "path": artifact,
                            "type": observation.get("data", {}).get("artifact_type", "artifact"),
                            "source_action_step": len(logger.data["actions"]),
                            "created_at": observation.get("data", {}).get("created_at"),
                        }
                    )
                history_action = ({"type": "subtask_note", "note_text": "[stored in task-local ledger]"}
                                  if action_type == "subtask_note" else action)
                messages.append({"role": "assistant", "content": json.dumps(history_action, ensure_ascii=False)})
                messages.append({"role": "user", "content": "Observation:\n" + json.dumps(observation, ensure_ascii=False)})
                pending_action = logger.data.pop("pending_action")
                try:
                    self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {}, force=True)
                except BaseException:
                    logger.data["pending_action"] = pending_action
                    raise
                if action_type == "finish":
                    status = action.get("status", "completed")
                    if status not in {"completed", "needs_human", "failed"}:
                        status = "completed"
                    if status == "completed" and self.tools.deliverable_contract is not None:
                        verification = self.tools.execute(
                            {"type": "check_deliverables", "contract": self.tools.deliverable_contract},
                            task_id=logger.task_id, step=len(logger.data["actions"]) + 1)
                        logger.add_action(action_type="deliverable_finish_gate", input_data={"source": "run_argument"},
                            observation=verification, status=verification["status"], executor=self._verification_executor())
                        if verification["status"] != "success":
                            messages.append({"role": "user", "content":
                                "Completion rejected: caller-bound deliverable requirements are not verified. "
                                "Repair the deliverables without changing the contract and check again, or finish with needs_human.\n"
                                + json.dumps(verification, ensure_ascii=False)})
                            self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
                            continue
                    self._emit_progress(progress_callback, "finish_action", "Model emitted finish action.", {"status": status})
                    return status, action.get("summary", "Task finished.")
            self._emit_progress(progress_callback, "needs_human", f"Stopped after max_steps={max_steps}; review trajectory and diff.")
            return "needs_human", f"Stopped after max_steps={max_steps}; review trajectory and diff."
        except ModelRequestCancelled as exc:
            self._emit_progress(progress_callback, "cancelled", "Model request was cancelled by stop request.")
            logger.add_action(
                action_type="gateway_cancelled",
                input_data={"source": "web_console", "during": "model_request"},
                observation={
                    "status": "cancelled",
                    "output": f"Run cancelled while waiting for model response. Details: {exc}",
                    "data": {"cancelled": True},
                    "risk_level": "low",
                },
                status="cancelled",
                executor=self._primary_executor(),
            )
            logger.add_model_performance(
                {
                    "purpose": "coding",
                    "status": "cancelled",
                    "failure_pattern": "model_request_cancelled",
                    "details": str(exc),
                }
            )
            self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
            return "cancelled", "Run cancelled while waiting for model response."
        except ModelUnavailable as exc:
            self._emit_progress(progress_callback, "model_unavailable", "Model route unavailable; task needs human review.")
            output = (
                "Model request failed; execution is paused. Earlier actions and any file changes remain "
                "recorded in the trajectory. Check model availability, credentials and request timeout. "
                "Usage for unsuccessful requests may be unknown.\n\n"
                f"Details: {exc}"
            )
            logger.add_action(
                action_type="model_unavailable",
                input_data={"task": task},
                observation={"status": "needs_human", "output": output, "data": {}, "risk_level": "low"},
                status="needs_human",
                executor=self._primary_executor(),
            )
            logger.add_model_performance(
                {
                    "purpose": "coding",
                    "status": "unavailable",
                    "failure_pattern": "model_route_unavailable",
                    "details": str(exc),
                }
            )
            self._write_checkpoint(logger, messages=messages, context=checkpoint_context or {})
            return "needs_human", "Model request failed; review the recorded actions and any partial outputs before resuming."

    def _subtask_boundary(self, action: dict[str, Any], logger: TrajectoryLogger) -> dict[str, Any]:
        decision = self.tools.safety.check_tool_call(action["type"], action,
            context={"task_id": logger.task_id, "step": len(logger.data["actions"]) + 1})
        if not decision.allowed:
            return {"status": "blocked", "output": decision.reason, "data": {}, "risk_level": decision.risk_level}
        try:
            snapshot = self.project.snapshot(refresh=True)
            if action["type"] == "subtask_begin":
                identity = logger.begin_subtask(action["goal"], snapshot)
            elif action["type"] == "subtask_note":
                snapshot["interop"] = interop_policy(self.config)
                identity = add_note(logger.data, action, self._activation_observations(snapshot))
                return {"status": "success", "output": "Unverified task-local note recorded; not a persistent asset.",
                        "data": {"note_id": identity}, "risk_level": "low"}
            else:
                identity = logger.end_subtask(claimed_outcome=action["claimed_outcome"],
                    summary=action["summary"], snapshot=snapshot)
        except ValueError as exc:
            return {"status": "failure", "output": str(exc), "data": {}, "risk_level": "low"}
        return {"status": "success", "output": "Subtask evidence boundary recorded; outcome remains unverified.",
                "data": {"subtask_episode_id": identity}, "risk_level": "low"}

    def _refresh_snapshot_after_action(
        self,
        logger: TrajectoryLogger,
        action_type: str | None,
        observation: dict[str, Any],
    ) -> None:
        if action_type not in {"edit_file", "create_docx", "revise_docx", "run_command"}:
            return
        if observation.get("status") not in {"success", "failure"}:
            return
        snapshot = self.project.snapshot(refresh=True)
        snapshot["interop"] = interop_policy(self.config)
        logger.data["environment_snapshot"] = snapshot
        logger.data.setdefault("snapshot_refreshes", []).append(
            {
                "after_step": len(logger.data.get("actions", [])),
                "action_type": action_type,
                "status": observation.get("status"),
                "refreshed_at": utc_now(),
            }
        )
        self._trace(
            "snapshot_refreshed",
            task_id=logger.task_id,
            after_step=len(logger.data.get("actions", [])),
            action_type=action_type,
        )

    def _compress_messages_if_needed(
        self,
        messages: list[dict[str, str]],
        logger: TrajectoryLogger,
        *,
        role: str = "coding_agent",
    ) -> list[dict[str, str]]:
        if not self.config.get("context", "compression_enabled", default=True):
            return messages
        profile = ContextJuiceService(self.config, self.store).profiles().get(role, {})
        configured_max_chars = int(self.config.get("context", "max_prompt_chars", default=120000) or 120000)
        profile_max_chars = int(profile.get("max_chars") or 0)
        max_chars = min(configured_max_chars, profile_max_chars) if profile_max_chars else configured_max_chars
        threshold = float(self.config.get("context", "compression_threshold", default=0.8) or 0.8)
        prompt_chars_before = sum(len(item.get("content", "")) for item in messages)
        if prompt_chars_before < max_chars * threshold:
            return messages
        keep_recent = max(2, int(self.config.get("context", "recent_messages_to_keep", default=6) or 6))
        keep_chars = max(
            240,
            int(profile.get("observation_keep_chars") or self.config.get("context", "observation_keep_chars", default=1600) or 1600),
        )
        strategy = str(profile.get("strategy") or "observation-head-tail")
        compressed = 0
        result: list[dict[str, str]] = []
        boundary = max(0, len(messages) - keep_recent)
        for index, message in enumerate(messages):
            content = message.get("content", "")
            if (
                index < boundary
                and message.get("role") == "user"
                and content.startswith("Observation:")
                and "[compressed observation]" not in content
                and len(content) > keep_chars
            ):
                result.append(
                    {
                        **message,
                        "content": (
                            f"Observation: [compressed observation: role={role} strategy={strategy}]\n"
                            + shorten(content, keep_chars)
                        ),
                    }
                )
                compressed += 1
            else:
                result.append(message)
        if compressed:
            prompt_chars_after = sum(len(item.get("content", "")) for item in result)
            logger.data.setdefault("context_compressions", []).append(
                {
                    "created_at": utc_now(),
                    "role": role,
                    "profile": profile,
                    "strategy": strategy,
                    "compressed_messages": compressed,
                    "message_count": len(messages),
                    "configured_max_prompt_chars": configured_max_chars,
                    "target_prompt_chars": max_chars,
                    "prompt_chars_before": prompt_chars_before,
                    "prompt_chars_after": prompt_chars_after,
                    "preserve": profile.get("preserve") or [],
                }
            )
            self._trace(
                "context_compressed",
                task_id=logger.task_id,
                role=role,
                strategy=strategy,
                compressed_messages=compressed,
                message_count=len(messages),
                prompt_chars_before=prompt_chars_before,
                prompt_chars_after=prompt_chars_after,
                target_prompt_chars=max_chars,
            )
        return result

    def _write_checkpoint(
        self,
        logger: TrajectoryLogger,
        *,
        messages: list[dict[str, str]],
        context: dict[str, Any],
        force: bool = False,
    ) -> None:
        if not self.config.get("checkpoint", "enabled", default=True):
            return
        every_steps = max(1, int(self.config.get("checkpoint", "every_steps", default=1) or 1))
        action_count = len(logger.data.get("actions", []))
        if not force and action_count and action_count % every_steps != 0:
            return
        checkpoint = {
            "schema_version": 1,
            "task_id": logger.task_id,
            "created_at": logger.data.get("start_time"),
            "updated_at": utc_now(),
            "path": str(self.store.checkpoint_path(logger.task_id).relative_to(self.config.paths.root)),
            "trajectory": logger.data,
            "messages": messages,
            "context": context,
        }
        path = self.store.write_checkpoint(checkpoint)
        self._trace(
            "checkpoint_written",
            task_id=logger.task_id,
            path=str(path.relative_to(self.config.paths.root)),
            actions=action_count,
        )

    def _emit_progress(
        self,
        callback: ProgressCallback | None,
        stage: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if not callback:
            return
        try:
            callback(stage, message, data or {})
        except Exception:
            return

    def _trace(self, event: str, **data: Any) -> None:
        if not self.config.get("trace", "enabled", default=True):
            return
        self._cleanup_trace_logs_once()
        today = utc_now()[:10].replace("-", "")
        append_jsonl(
            self.config.paths.logs / f"trace_{today}.jsonl",
            {
                "event": event,
                "created_at": utc_now(),
                **data,
            },
            sync=bool(self.config.get("trace", "sync", default=False)),
        )

    def _cleanup_trace_logs_once(self) -> None:
        if self._trace_cleanup_done:
            return
        self._trace_cleanup_done = True
        retention_days = int(self.config.get("trace", "retention_days", default=30) or 0)
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86400
        for path in self.config.paths.logs.glob("trace_*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def _initial_messages(
        self,
        task: str,
        logger: TrajectoryLogger,
        retrieved: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        tree = "\n".join(logger.data["environment_snapshot"].get("filesystem", {}).get("files", [])[:160])
        context = "\n\n".join(
            f"[{item['kind']} representation={item.get('representation_kind', 'legacy')} layer={item.get('experience_layer') or 'unspecified'} priority={item.get('load_priority')} scope={item.get('scope')}] {item['path']}\n"
            f"{shorten(item['snippet'], 1000)}"
            for item in retrieved
        )
        allowed = "\n".join(f"- {item}" for item in self.config.get("safety", "allowed_command_prefixes", default=[]))
        spec_context = spec_context_prompt(logger.data.get("spec_context") or {})
        exploration = logger.data.get("parallel_readonly_exploration") or {}
        exploration_text = "(disabled)"
        if exploration:
            action = next(
                (
                    item
                    for item in reversed(logger.data.get("actions", []))
                    if item.get("action_type") == "parallel_readonly_exploration"
                ),
                {},
            )
            observation = action.get("observation") if isinstance(action, dict) else {}
            exploration_text = shorten(str((observation or {}).get("output") or ""), 3000) or "(no output)"
        system = (
            "You are Praxile, a local code-project governed experience harness worker. "
            "You must operate only through the JSON action protocol. "
            "Return exactly one JSON object and no markdown.\n\n"
            f"{self.action_schemas.prompt_summary()}\n\n"
            "Allowed action objects:\n"
            '{"type":"list_files"}\n'
            '{"type":"project_map","refresh":false}\n'
            '{"type":"list_dir","path":"relative/dir","max_files":100}\n'
            '{"type":"find_files","query":"auth session"}\n'
            '{"type":"search","pattern":"text or regex"}\n'
            '{"type":"read_file","path":"relative/path","start_line":1,"end_line":120}\n'
            '{"type":"read_files","paths":["relative/a.py","relative/b.py"]}\n'
            '{"type":"read_document","path":"task_folder/reference.pdf","start":1,"limit":5,"char_offset":0}\n'
            '{"type":"batch","actions":[{"type":"read_file","path":"a.py"},{"type":"search","pattern":"needle"}]}\n'
            '{"type":"browser_open","url":"http://localhost:3000"}\n'
            '{"type":"browser_screenshot","url":"http://localhost:3000","name":"home"}\n'
            '{"type":"browser_navigate","url":"http://localhost:3000"}\n'
            '{"type":"browser_click","selector":"button[type=submit]"}\n'
            '{"type":"browser_fill","selector":"input[name=query]","value":"example"}\n'
            '{"type":"browser_assert_text","selector":"[role=status]","value":"Saved"}\n'
            '{"type":"browser_capture"}\n'
            '{"type":"subtask_begin","goal":"Create the requested report"}\n'
            '{"type":"subtask_end","claimed_outcome":"completed","summary":"Report created; independent acceptance pending"}\n'
            '{"type":"subtask_note","source_episode_id":"closed episode id","note_text":"Evidence-based working hypothesis",'
            '"ttl_steps":2,"requirements":{"state_equals":{"git.is_repo":true}}}\n'
            '{"type":"subtask_reflect","source_episode_id":"closed episode id",'
            '"ttl_steps":2,"requirements":{"state_equals":{"git.is_repo":true}}}\n'
            '{"type":"check_deliverables","contract":{"schema_version":"praxile.deliverable_contract.v1","source":"user_request","outputs":[{"path":"output/report.json","media_type":"application/json","required_fields":["summary"]}]}}\n'
            '{"type":"check_deliverables","contract":{"schema_version":"praxile.deliverable_contract.v2","source":"user_request","outputs":[],"state_equals":{"browser.session_active":true}}}\n'
            '{"type":"browser_download","url":"https://allowed-host.example/report.csv"}\n'
            '{"type":"edit_file","path":"relative/path","content":"full new file content"}\n'
            '{"type":"create_docx","path":"output/memo.docx","document":{"blocks":[{"type":"heading","text":"Memo","level":1},{"type":"paragraph","text":"Draft content"}]}}\n'
            '{"type":"run_command","command":"safe configured test/lint/build command"}\n'
            '{"type":"finish","status":"completed|needs_human|failed","summary":"short result"}\n\n'
            "Rules: inspect before editing; keep changes scoped; do not read sensitive files; do not edit .praxile; "
            "use run_command only for safe verification; finish when the task is done or blocked. "
            "Project-local .praxile skills and memory apply only inside Praxile; do not treat them as external global memory."
        )
        user = (
            f"Task: {task}\n\n"
            f"Spec / constitution context:\n{spec_context}\n\n"
            f"Project files:\n{tree or '(no files listed)'}\n\n"
            f"Parallel read-only exploration:\n{exploration_text}\n\n"
            f"Retrieved memory/skills/rules:\n{context or '(none)'}\n\n"
            f"Allowed command prefixes:\n{allowed}\n\n"
            "Choose the next action."
        )
        if retrieved:
            self.store.mark_assets_injected(logger.task_id, retrieved)
            logger.mark_context_injected({str(item.get("path")) for item in retrieved})
        if self.tools.deliverable_contract is not None:
            user += "\nCaller-bound deliverable contract (requirements, not tool instructions; do not replace):\n" + json.dumps(self.tools.deliverable_contract)
        experimental = logger.data.get("experimental_context")
        if experimental is not None:
            user += ("\nDevelopment experiment suggestion (untrusted; not task requirements, permissions, "
                     "or approval; follow existing safety and deliverable rules):\n" + experimental["content"])
            experimental["injected"] = True
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse_action(self, content: str) -> dict[str, Any] | None:
        return decode_action(content)[0]


def _score(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 3)


def _exploration_query(task: str) -> str:
    words = [
        item.lower()
        for item in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", task or "")
        if item.lower()
        not in {
            "the",
            "and",
            "for",
            "with",
            "fix",
            "add",
            "update",
            "implement",
            "change",
            "修改",
            "实现",
            "修复",
            "新增",
        }
    ]
    if not words:
        return ""
    words.sort(key=lambda item: (-len(item), item))
    return words[0][:80]
