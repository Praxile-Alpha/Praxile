from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import (
    CONFIG_FILE,
    DANGEROUS_COMMAND_PATTERNS,
    DEFAULT_ALLOWED_COMMAND_PREFIXES,
    PRAXILE_DIR,
    SENSITIVE_GLOBS,
)
from .json_utils import RobustJSONError, parse_jsonc_object


class ConfigValidationError(ValueError):
    pass


def default_config(project_root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "project": {
            "name": project_root.name,
            "root": ".",
        },
        "runtime": {
            "mode": "safe",
            "max_steps": 10,
            "model_timeout_seconds": 30,
            "online_check_timeout_seconds": 8,
            "shell_timeout_seconds": 120,
            "test_timeout_seconds": 180,
            "action_parse_retries": 2,
            "invalid_action_fail_fast_count": 3,
            "require_diff_review": True,
            "default_test_commands": [],
        },
        "checkpoint": {
            "enabled": True,
            "every_steps": 1,
        },
        "executors": {
            "primary_executor_id": "coding_agent",
            "parallel_readonly_exploration_enabled": False,
            "max_readonly_concurrency": 8,
            "readonly_executor_prefix": "readonly_explorer",
        },
        "context": {
            "compression_enabled": True,
            "max_prompt_chars": 120000,
            "compression_threshold": 0.8,
            "observation_keep_chars": 1600,
            "recent_messages_to_keep": 6,
        },
        "trace": {
            "enabled": True,
            "sync": False,
            "retention_days": 30,
        },
        "task_analysis": {
            "llm_assisted": False,
            "llm_model_role": "review_recommendation",
            "llm_timeout_seconds": 12,
            "llm_max_tokens": 800,
        },
        "semantic_judges": {
            "enabled": False,
            "local_first": True,
            "max_calls_per_run": 5,
            "max_calls_per_mine_patterns": 20,
            "feedback_classifier": {
                "enabled": True,
                "role": "feedback_classifier",
                "use_for_complex_feedback_only": True,
                "timeout_seconds": 12,
                "max_tokens": 800,
            },
            "attribution_judge": {
                "enabled": True,
                "role": "attribution_judge",
                "only_for_loaded_assets_with_score_above": 0.5,
                "max_assets_per_run": 4,
                "timeout_seconds": 12,
                "max_tokens": 900,
            },
            "pattern_mining": {
                "enabled": True,
                "role": "pattern_mining",
                "only_after_heuristic_score": 0.45,
                "timeout_seconds": 12,
                "max_tokens": 900,
            },
            "counterexample_checker": {
                "enabled": True,
                "role": "counterexample_checker",
                "timeout_seconds": 12,
                "max_tokens": 900,
            },
        },
        "search": {
            "backend": "auto",
            "timeout_seconds": 30,
            "default_limit": 80,
        },
        "project_map": {
            "cache_enabled": True,
            "cache_ttl_seconds": 30,
        },
        "index": {
            "fts_enabled": True,
        },
        "retrieval": {
            "hybrid_enabled": False,
            "vector_enabled": False,
            "vector_provider": "local_hash",
            "embedding_provider": "local_hash",
            "embedding_model": None,
            "vector_dims": 256,
            "vector_min_score": 0.05,
            "keyword_priority_weight": 0.05,
            "vector_priority_weight": 0.03,
            "hybrid_rank_boost": 1.0,
            "usage_log_weight": 0.02,
            "positive_outcome_weight": 0.10,
            "negative_outcome_weight": 0.20,
            "stale_usage_days": 90,
            "stale_usage_penalty": 0.10,
        },
        "evolution": {
            "llm_assisted_proposals": False,
            "llm_model_role": "proposal_composer",
            "llm_timeout_seconds": 20,
            "llm_max_tokens": 1800,
            "consolidation_min_duplicates": 2,
            "consolidation_stale_days": 90,
            "consolidation_low_value_max_confidence": 0.4,
        },
        "reflect": {
            "stale_days": 90,
            "duplicate_min_assets": 2,
            "silent_failure_min_count": 2,
            "rejected_theme_min_count": 2,
            "high_value_positive_min": 3,
            "max_findings": 50,
            "ci": {
                "default_since": "7d",
                "artifact_dir": ".praxile/experience/reflect/ci",
                "max_findings": None,
                "max_high_severity": 0,
                "max_generated_proposals": None,
                "write_github_step_summary": True,
            },
        },
        "proposal_gate": {
            "enabled": True,
            "min_confidence": 0.55,
        },
        "memory": {
            "shard_enabled": True,
            "project_memory_soft_limit_bytes": 200000,
            "shard_by": "month",
        },
        "browser": {
            "enabled": False,
            "adapter": "playwright",
            "dev_server_url": None,
            "artifact_dir": ".praxile/experience/artifacts",
            "allowed_hosts": ["localhost", "127.0.0.1", "::1"],
            "timeout_ms": 15000,
            "viewport_width": 1280,
            "viewport_height": 900,
            "current_mvp": "playwright_optional_with_human_acceptance",
        },
        "architecture_gate": {
            "shadow_mode": False,
        },
        "workspace": {
            "default_mode": "in-place",
            "keep_after_run": True,
            "copy_excludes": [
                ".git",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                "dist",
                "build",
                ".next",
            ],
        },
        "interop_guardrails": {
            "refuse_writes_when_external_agent_detected": True,
            "lock_files": [
                ".hermes/agent.lock",
                ".openclaw/agent.lock",
                ".codex/agent.lock",
                ".agent.lock",
            ],
            "environment_flags": [
                "HERMES_AGENT_ACTIVE",
                "OPENCLAW_AGENT_ACTIVE",
                "PRAXILE_EXTERNAL_AGENT_ACTIVE",
            ],
        },
        "model": {
            "transport": "auto",
            "timeout_seconds": 30,
            "max_retries": 1,
            "retry_backoff_seconds": 0.25,
            "streaming": False,
        },
        "safety": {
            "sensitive_globs": SENSITIVE_GLOBS,
            "dangerous_command_patterns": DANGEROUS_COMMAND_PATTERNS,
            "allowed_command_prefixes": DEFAULT_ALLOWED_COMMAND_PREFIXES,
            "protected_paths": [PRAXILE_DIR],
            "backup_max_files": 500,
            "backup_max_bytes": 200_000_000,
        },
        "shell": {
            "allow_shell_features": False,
        },
        "model_providers": {
        },
        "model_roles": {
            "embedding": {
                "provider": "local",
                "model": "local_hash",
            },
        },
        "cost_control": {
            "local_first": True,
            "prefer_ollama_for": [
                "evidence_extraction",
                "experience_reflection",
                "reward_judge",
                "proposal_composer",
                "review_recommendation",
                "cheap_reasoner",
                "feedback_classifier",
                "attribution_judge",
                "counterexample_checker",
                "pattern_mining",
                "project_pattern_composer",
            ],
            "use_cloud_for": [
                "coding_agent",
                "deep_project_pattern_mining",
            ],
            "max_cloud_calls_per_run": 3,
            "max_cost_per_run_usd": 0.05,
        },
        "routing": {
            "fallback_backoff_seconds": 0.25,
            "fallbacks": {},
            "strategy": {
                "privacy_sensitive": "configure a local/private model role before running privacy-sensitive tasks",
                "complex_planning": "configure coding_agent or planning_model with a stronger model when needed",
                "coding": "configure model_roles.coding_agent before autonomous code edits",
                "experience_extraction": "configure local model roles for low-cost project learning",
                "high_risk": "configure a strong model role and require human approval",
            },
        },
        "reward": {
            "mode": "hybrid",
            "llm_judge_enabled": False,
            "manual_acceptance_required": True,
            "min_experience_value_for_proposals": 0.5,
            "weights": {
                "objective": 0.60,
                "user_feedback": 0.30,
                "llm_judge": 0.10,
                "task_success": 0.30,
                "process_safety": 0.20,
                "regression": 0.25,
                "cost": 0.10,
                "experience_value": 0.15,
            },
            "user_feedback": {
                "enabled": True,
                "collect_from_chat": True,
                "default_target": "latest_run",
                "require_confirmation_for_asset_negative_feedback": True,
                "positive_reward_delta": 0.25,
                "negative_reward_delta": -0.35,
            },
            "llm_judge": {
                "enabled": False,
                "role": "reward_judge",
                "max_cost_per_run": 0.01,
                "timeout_seconds": 12,
                "max_tokens": 800,
            },
            "scores": {
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
            },
            "cost_thresholds": {
                "medium_tool_calls": 12,
                "high_tool_calls": 20,
                "medium_model_calls": 8,
                "high_model_calls": 12,
            },
            "scope": {
                "broad_edit_top_level_threshold": 4,
            },
        },
        "gateway": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 8765,
            "max_threads": 16,
            "token_env": "PRAXILE_GATEWAY_TOKEN",
            "channels_enabled": False,
        },
        "channels": {
            "version": 1,
            "default": None,
            "bindings": {},
            "platforms": {
                "telegram": {
                    "enabled": False,
                    "token_env": "TELEGRAM_BOT_TOKEN",
                    "home_channel": None,
                    "require_mention": True,
                    "mention_patterns": ["@praxile"],
                    "free_response_chats": [],
                    "ignored_threads": [],
                    "group_allowed_chats": [],
                    "reactions": True,
                    "disable_link_previews": True,
                    "proxy_url_env": "TELEGRAM_PROXY",
                },
                "discord": {
                    "enabled": False,
                    "token_env": "DISCORD_BOT_TOKEN",
                    "home_channel": None,
                    "require_mention": True,
                    "free_response_channels": [],
                    "allowed_channels": [],
                    "ignored_channels": [],
                    "no_thread_channels": [],
                    "auto_thread": True,
                    "reactions": True,
                    "allow_mentions": {
                        "users": True,
                        "roles": False,
                        "everyone": False,
                    },
                    "channel_skill_bindings": {},
                    "channel_prompts": {},
                },
            },
        },
    }


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / PRAXILE_DIR

    @property
    def config(self) -> Path:
        return self.state / CONFIG_FILE

    @property
    def db(self) -> Path:
        return self.state / "db" / "index.sqlite"

    @property
    def trajectories(self) -> Path:
        return self.state / "experience" / "trajectories"

    @property
    def feedback(self) -> Path:
        return self.state / "experience" / "feedback"

    @property
    def proposals_pending(self) -> Path:
        return self.state / "experience" / "proposals" / "pending"

    @property
    def proposals_accepted(self) -> Path:
        return self.state / "experience" / "proposals" / "accepted"

    @property
    def proposals_rejected(self) -> Path:
        return self.state / "experience" / "proposals" / "rejected"

    @property
    def backups(self) -> Path:
        return self.state / "backups"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def checkpoints(self) -> Path:
        return self.state / "checkpoints"


class Config:
    def __init__(self, data: dict[str, Any], paths: ProjectPaths):
        self.data = data
        self.paths = paths

    @classmethod
    def load(cls, project_root: Path) -> "Config":
        paths = ProjectPaths(project_root.resolve())
        config_path = paths.config
        legacy_path = paths.state / "config.yaml"
        if not config_path.exists() and legacy_path.exists():
            config_path = legacy_path
        if not config_path.exists():
            return cls(default_config(paths.root), paths)
        text = config_path.read_text(encoding="utf-8")
        try:
            data = parse_jsonc_object(text)
        except RobustJSONError as exc:
            raise ValueError(
                f"{config_path} must be valid JSON/JSONC. "
                "Run `praxile init --force` to regenerate .praxile/config.json."
            ) from exc
        merged = default_config(paths.root)
        deep_update(merged, data)
        validate_config(merged, source=config_path)
        return cls(merged, paths)

    def write(self) -> None:
        self.paths.config.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur


def deep_update(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def validate_config(data: dict[str, Any], *, source: Path | None = None) -> None:
    errors: list[str] = []

    def expect(path: str, expected: type | tuple[type, ...]) -> None:
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                errors.append(f"{path}: missing")
                return
            cur = cur[part]
        if not isinstance(cur, expected):
            names = ", ".join(t.__name__ for t in expected) if isinstance(expected, tuple) else expected.__name__
            errors.append(f"{path}: expected {names}, got {type(cur).__name__}")

    for path in [
        "runtime.max_steps",
        "runtime.model_timeout_seconds",
        "runtime.online_check_timeout_seconds",
        "runtime.shell_timeout_seconds",
        "runtime.test_timeout_seconds",
        "runtime.action_parse_retries",
        "runtime.invalid_action_fail_fast_count",
        "safety.backup_max_files",
        "safety.backup_max_bytes",
        "checkpoint.every_steps",
        "executors.max_readonly_concurrency",
        "context.max_prompt_chars",
        "context.observation_keep_chars",
        "context.recent_messages_to_keep",
        "memory.project_memory_soft_limit_bytes",
        "trace.retention_days",
        "gateway.max_threads",
        "evolution.consolidation_min_duplicates",
        "evolution.consolidation_stale_days",
        "reflect.stale_days",
        "reflect.duplicate_min_assets",
        "reflect.silent_failure_min_count",
        "reflect.rejected_theme_min_count",
        "reflect.high_value_positive_min",
        "reflect.max_findings",
        "retrieval.stale_usage_days",
        "search.timeout_seconds",
        "cost_control.max_cloud_calls_per_run",
        "reward.llm_judge.timeout_seconds",
        "reward.llm_judge.max_tokens",
        "semantic_judges.max_calls_per_run",
        "semantic_judges.max_calls_per_mine_patterns",
        "semantic_judges.feedback_classifier.timeout_seconds",
        "semantic_judges.feedback_classifier.max_tokens",
        "semantic_judges.attribution_judge.max_assets_per_run",
        "semantic_judges.attribution_judge.timeout_seconds",
        "semantic_judges.attribution_judge.max_tokens",
        "semantic_judges.pattern_mining.timeout_seconds",
        "semantic_judges.pattern_mining.max_tokens",
        "semantic_judges.counterexample_checker.timeout_seconds",
        "semantic_judges.counterexample_checker.max_tokens",
    ]:
        expect(path, int)
    for path in [
        "checkpoint.enabled",
        "executors.parallel_readonly_exploration_enabled",
        "context.compression_enabled",
        "trace.enabled",
        "trace.sync",
        "retrieval.hybrid_enabled",
        "retrieval.vector_enabled",
        "evolution.llm_assisted_proposals",
        "proposal_gate.enabled",
        "memory.shard_enabled",
        "shell.allow_shell_features",
        "cost_control.local_first",
        "reward.user_feedback.enabled",
        "reward.user_feedback.collect_from_chat",
        "reward.user_feedback.require_confirmation_for_asset_negative_feedback",
        "reward.llm_judge.enabled",
        "semantic_judges.enabled",
        "semantic_judges.local_first",
        "semantic_judges.feedback_classifier.enabled",
        "semantic_judges.feedback_classifier.use_for_complex_feedback_only",
        "semantic_judges.attribution_judge.enabled",
        "semantic_judges.pattern_mining.enabled",
        "semantic_judges.counterexample_checker.enabled",
        "workspace.keep_after_run",
        "reflect.ci.write_github_step_summary",
    ]:
        expect(path, bool)
    for path in [
        "context.compression_threshold",
        "retrieval.vector_min_score",
        "retrieval.keyword_priority_weight",
        "retrieval.vector_priority_weight",
        "retrieval.hybrid_rank_boost",
        "retrieval.usage_log_weight",
        "retrieval.positive_outcome_weight",
        "retrieval.negative_outcome_weight",
        "retrieval.stale_usage_penalty",
        "routing.fallback_backoff_seconds",
        "reward.weights.task_success",
        "reward.weights.objective",
        "reward.weights.user_feedback",
        "reward.weights.llm_judge",
        "reward.weights.process_safety",
        "reward.weights.regression",
        "reward.weights.cost",
        "reward.weights.experience_value",
        "reward.scores.default_task_success",
        "reward.scores.completed_with_edits",
        "reward.scores.completed_without_edits",
        "reward.scores.needs_human",
        "reward.scores.failed",
        "reward.scores.safe_process",
        "reward.scores.blocked_process",
        "reward.scores.high_risk_blocked_process",
        "reward.scores.tests_passed",
        "reward.scores.tests_failed",
        "reward.scores.tests_detected_not_run",
        "reward.scores.no_tests_available",
        "reward.scores.default_regression",
        "reward.scores.low_cost",
        "reward.scores.medium_cost",
        "reward.scores.high_cost",
        "reward.scores.experience_with_signal",
        "reward.scores.experience_without_signal",
        "reward.scores.scope_control_default",
        "reward.scores.scope_control_no_edits",
        "reward.scores.scope_control_broad_edits",
        "reward.scores.scope_control_failed_or_blocked",
        "reward.min_experience_value_for_proposals",
        "evolution.consolidation_low_value_max_confidence",
        "proposal_gate.min_confidence",
        "cost_control.max_cost_per_run_usd",
        "reward.user_feedback.positive_reward_delta",
        "reward.user_feedback.negative_reward_delta",
        "reward.llm_judge.max_cost_per_run",
        "semantic_judges.attribution_judge.only_for_loaded_assets_with_score_above",
        "semantic_judges.pattern_mining.only_after_heuristic_score",
    ]:
        expect(path, (int, float))
    for path in [
        "reward.cost_thresholds.medium_tool_calls",
        "reward.cost_thresholds.high_tool_calls",
        "reward.cost_thresholds.medium_model_calls",
        "reward.cost_thresholds.high_model_calls",
        "reward.scope.broad_edit_top_level_threshold",
    ]:
        expect(path, int)
    for path in [
        "runtime.default_test_commands",
        "safety.sensitive_globs",
        "safety.dangerous_command_patterns",
        "safety.allowed_command_prefixes",
        "cost_control.prefer_ollama_for",
        "cost_control.use_cloud_for",
        "workspace.copy_excludes",
    ]:
        expect(path, list)
    for path in [
        "model_providers",
        "model_roles",
        "routing",
        "routing.fallbacks",
        "channels",
        "executors",
        "proposal_gate",
        "reflect",
        "reflect.ci",
        "semantic_judges",
        "semantic_judges.feedback_classifier",
        "semantic_judges.attribution_judge",
        "semantic_judges.pattern_mining",
        "semantic_judges.counterexample_checker",
    ]:
        expect(path, dict)
    for path in [
        "runtime.mode",
        "executors.primary_executor_id",
        "executors.readonly_executor_prefix",
        "search.backend",
        "retrieval.vector_provider",
        "model.transport",
        "reward.mode",
        "reward.user_feedback.default_target",
        "reward.llm_judge.role",
        "semantic_judges.feedback_classifier.role",
        "semantic_judges.attribution_judge.role",
        "semantic_judges.pattern_mining.role",
        "semantic_judges.counterexample_checker.role",
        "gateway.host",
        "gateway.token_env",
        "workspace.default_mode",
        "reflect.ci.default_since",
        "reflect.ci.artifact_dir",
    ]:
        expect(path, str)
    for path in [
        "browser.dev_server_url",
        "retrieval.embedding_model",
        "channels.default",
    ]:
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                errors.append(f"{path}: missing")
                cur = None
                break
            cur = cur[part]
        if cur is not None and not isinstance(cur, str):
            errors.append(f"{path}: expected str or null, got {type(cur).__name__}")

    def value_at(path: str, default: Any = None) -> Any:
        cur: Any = data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    for path in [
        "runtime.max_steps",
        "runtime.model_timeout_seconds",
        "runtime.online_check_timeout_seconds",
        "runtime.shell_timeout_seconds",
        "runtime.test_timeout_seconds",
        "runtime.action_parse_retries",
        "runtime.invalid_action_fail_fast_count",
        "executors.max_readonly_concurrency",
        "trace.retention_days",
        "gateway.max_threads",
        "reward.scope.broad_edit_top_level_threshold",
        "evolution.consolidation_min_duplicates",
        "evolution.consolidation_stale_days",
        "reflect.stale_days",
        "reflect.duplicate_min_assets",
        "reflect.silent_failure_min_count",
        "reflect.rejected_theme_min_count",
        "reflect.high_value_positive_min",
        "reflect.max_findings",
        "retrieval.stale_usage_days",
        "search.timeout_seconds",
    ]:
        value = value_at(path)
        if isinstance(value, int) and value <= 0:
            errors.append(f"{path}: must be positive")
    for path in [
        "reflect.ci.max_findings",
        "reflect.ci.max_high_severity",
        "reflect.ci.max_generated_proposals",
    ]:
        value = value_at(path)
        if value is not None and not isinstance(value, int):
            errors.append(f"{path}: expected int or null, got {type(value).__name__}")
        if isinstance(value, int) and value < 0:
            errors.append(f"{path}: must be >= 0")
    min_experience = value_at("reward.min_experience_value_for_proposals")
    if isinstance(min_experience, (int, float)) and float(min_experience) < 0:
        errors.append("reward.min_experience_value_for_proposals: must be non-negative")
    reward_mode = value_at("reward.mode")
    if reward_mode not in {"hybrid", "objective_plus_user", "objective_only"}:
        errors.append("reward.mode: expected hybrid, objective_plus_user, or objective_only")
    max_readonly = value_at("executors.max_readonly_concurrency")
    if isinstance(max_readonly, int) and not 1 <= max_readonly <= 16:
        errors.append("executors.max_readonly_concurrency: must be between 1 and 16")
    workspace_mode = value_at("workspace.default_mode")
    if workspace_mode not in {"in-place", "copy", "git-worktree"}:
        errors.append("workspace.default_mode: expected in-place, copy, or git-worktree")
    low_value_confidence = value_at("evolution.consolidation_low_value_max_confidence")
    if isinstance(low_value_confidence, (int, float)) and not 0 <= float(low_value_confidence) <= 1:
        errors.append("evolution.consolidation_low_value_max_confidence: must be between 0 and 1")
    gateway_port = value_at("gateway.port")
    if not isinstance(gateway_port, int):
        errors.append("gateway.port: expected int")
    elif not 1 <= gateway_port <= 65535:
        errors.append("gateway.port: must be between 1 and 65535")
    vector_dims = value_at("retrieval.vector_dims")
    if not isinstance(vector_dims, int):
        errors.append("retrieval.vector_dims: expected int")
    elif not 16 <= vector_dims <= 2048:
        errors.append("retrieval.vector_dims: must be between 16 and 2048")
    for key in [
        "keyword_priority_weight",
        "vector_priority_weight",
        "hybrid_rank_boost",
        "usage_log_weight",
        "positive_outcome_weight",
        "negative_outcome_weight",
        "stale_usage_penalty",
    ]:
        value = value_at(f"retrieval.{key}")
        if isinstance(value, (int, float)) and float(value) < 0:
            errors.append(f"retrieval.{key}: must be non-negative")
    weights = value_at("reward.weights", {})
    if isinstance(weights, dict):
        total = 0.0
        for key, value in weights.items():
            if isinstance(value, (int, float)):
                if value < 0:
                    errors.append(f"reward.weights.{key}: must be non-negative")
                total += float(value)
        if total <= 0:
            errors.append("reward.weights: at least one weight must be positive")
    providers = value_at("model_providers", {})
    if isinstance(providers, dict):
        for provider_name, provider in providers.items():
            if not isinstance(provider, dict):
                errors.append(f"model_providers.{provider_name}: expected object")
                continue
            provider_type = provider.get("type", "openai_compatible")
            if provider_type not in {"openai", "openai_compatible", "anthropic", "ollama"}:
                errors.append(
                    f"model_providers.{provider_name}.type: expected openai_compatible, openai, anthropic, or ollama"
                )
            base_url = provider.get("base_url")
            if base_url is not None and (not isinstance(base_url, str) or not urlparse(base_url).scheme):
                errors.append(f"model_providers.{provider_name}.base_url: expected absolute URL")
            models = provider.get("models")
            if not isinstance(models, list) or not models:
                errors.append(f"model_providers.{provider_name}.models: expected non-empty list")
            elif not all(
                isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("name"), str))
                for item in models
            ):
                errors.append(f"model_providers.{provider_name}.models: each model requires a string name")
    provider_names = set(providers.keys()) if isinstance(providers, dict) else set()
    provider_models: dict[str, set[str]] = {}
    if isinstance(providers, dict):
        for provider_name, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            models = provider.get("models")
            if not isinstance(models, list):
                continue
            names: set[str] = set()
            for item in models:
                if isinstance(item, str):
                    names.add(item)
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(item["name"])
            provider_models[provider_name] = names
    model_roles = value_at("model_roles", {})
    if isinstance(model_roles, dict):
        for role_name, role_config in model_roles.items():
            if not isinstance(role_config, dict):
                errors.append(f"model_roles.{role_name}: expected object")
                continue
            provider_name = role_config.get("provider")
            model_name = role_config.get("model")
            if provider_name == "local" and role_name == "embedding":
                if model_name != "local_hash":
                    errors.append("model_roles.embedding.model: local provider currently supports local_hash")
            else:
                if not isinstance(provider_name, str) or provider_name not in provider_names:
                    errors.append(f"model_roles.{role_name}.provider: unknown provider `{provider_name}`")
                if not isinstance(model_name, str) or not model_name:
                    errors.append(f"model_roles.{role_name}.model: expected non-empty string")
                elif provider_name in provider_models and model_name not in provider_models.get(str(provider_name), set()):
                    errors.append(f"model_roles.{role_name}.model: model `{model_name}` is not declared by provider `{provider_name}`")
            fallback = role_config.get("fallback", [])
            if fallback is None:
                fallback = []
            if not isinstance(fallback, list):
                errors.append(f"model_roles.{role_name}.fallback: expected list")
            else:
                for index, item in enumerate(fallback):
                    fb_provider: Any = None
                    fb_model: Any = None
                    if isinstance(item, str):
                        if ":" not in item:
                            errors.append(f"model_roles.{role_name}.fallback.{index}: expected provider:model")
                            continue
                        fb_provider, fb_model = item.split(":", 1)
                    elif isinstance(item, dict):
                        fb_provider = item.get("provider")
                        fb_model = item.get("model")
                    else:
                        errors.append(f"model_roles.{role_name}.fallback.{index}: expected object or provider:model string")
                        continue
                    if fb_provider == "local" and fb_model == "local_hash":
                        continue
                    if not isinstance(fb_provider, str) or fb_provider not in provider_names:
                        errors.append(f"model_roles.{role_name}.fallback.{index}.provider: unknown provider `{fb_provider}`")
                    if not isinstance(fb_model, str) or not fb_model:
                        errors.append(f"model_roles.{role_name}.fallback.{index}.model: expected non-empty string")
                    elif fb_provider in provider_models and fb_model not in provider_models.get(str(fb_provider), set()):
                        errors.append(
                            f"model_roles.{role_name}.fallback.{index}.model: "
                            f"model `{fb_model}` is not declared by provider `{fb_provider}`"
                        )
    routing = data.get("routing", {})
    if isinstance(routing, dict):
        for key, value in routing.items():
            if key in {"fallbacks", "strategy", "fallback_backoff_seconds"}:
                continue
            if isinstance(value, str):
                if ":" not in value:
                    errors.append(f"routing.{key}: expected provider:model")
                    continue
                provider_name, model_name = value.split(":", 1)
                if provider_name not in provider_names:
                    errors.append(f"routing.{key}: unknown provider `{provider_name}`")
                if not model_name:
                    errors.append(f"routing.{key}: model name is empty")
    channels = value_at("channels", {})
    if isinstance(channels, dict):
        bindings = channels.get("bindings", {})
        if isinstance(bindings, dict):
            for binding_id, binding in bindings.items():
                if not isinstance(binding, dict):
                    errors.append(f"channels.bindings.{binding_id}: expected object")
                    continue
                if binding.get("platform") not in {"telegram", "discord"}:
                    errors.append(f"channels.bindings.{binding_id}.platform: expected telegram or discord")
                if binding.get("mode") not in {"notify", "task", "bidirectional"}:
                    errors.append(f"channels.bindings.{binding_id}.mode: expected notify, task, or bidirectional")
                token_env = binding.get("token_env")
                if token_env is not None and not isinstance(token_env, str):
                    errors.append(f"channels.bindings.{binding_id}.token_env: expected string or null")
    host = value_at("gateway.host")
    token_env = value_at("gateway.token_env")
    if host in {"0.0.0.0", "::"} and not token_env:
        errors.append("gateway.host: non-local bind requires gateway.token_env")
    routing = data.get("routing", {})
    if isinstance(routing, dict):
        fallbacks = routing.get("fallbacks", {})
        if isinstance(fallbacks, dict):
            for key, value in fallbacks.items():
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    errors.append(f"routing.fallbacks.{key}: expected list[str]")
    if errors:
        prefix = f"{source}: " if source else ""
        raise ConfigValidationError(prefix + "invalid Praxile config:\n- " + "\n- ".join(errors))


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for path in [current, *current.parents]:
        if (path / PRAXILE_DIR).exists():
            return path
        if (path / ".git").exists():
            return path
    return current
