from __future__ import annotations

import copy
import os
import time
from typing import Any

from ..config import Config
from ..model import ModelError, ModelRouter, ModelUnavailable
from .errors import ServiceError


MODEL_ROLE_CATALOG = [
    ("coding_agent", "Core execution", "required", "Primary coding and action loop"),
    ("evidence_extraction", "Experience extraction", "recommended", "Extract reusable evidence from runs"),
    ("experience_reflection", "Experience extraction", "recommended", "Summarize learning and propose durable experience"),
    ("proposal_composer", "Experience extraction", "recommended", "Compose reviewable proposal text"),
    ("review_recommendation", "Review and reward", "recommended", "Classify review recommendations"),
    ("reward_judge", "Review and reward", "optional", "Optional quality judging"),
    ("feedback_classifier", "Review and reward", "recommended", "Route natural-language feedback"),
    ("attribution_judge", "Semantic judges", "optional", "Judge whether loaded assets influenced a run"),
    ("counterexample_checker", "Semantic judges", "optional", "Check pattern counterexamples"),
    ("pattern_mining", "Semantic judges", "optional", "Mine recurring cross-run patterns"),
    ("project_pattern_composer", "Semantic judges", "optional", "Compose high-quality project pattern cards"),
    ("deep_project_pattern_mining", "Semantic judges", "optional", "Higher-cost pattern mining"),
    ("cheap_reasoner", "Utility", "optional", "Low-cost local reasoning fallback"),
    ("embedding", "Utility", "required", "Retrieval embedding/vector role"),
]


SETUP_PRESETS: dict[str, dict[str, Any]] = {
    "minimal": {
        "label": "Minimal setup",
        "description": "Keep Praxile usable without autonomous coding models; local_hash embedding only.",
        "model_providers": {},
        "model_roles": {"embedding": {"provider": "local", "model": "local_hash"}},
    },
    "local-first": {
        "label": "Local-first",
        "description": "Use an Ollama-compatible local endpoint for coding and low-cost judges.",
        "model_providers": {
            "local_ollama": {
                "type": "ollama",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "OLLAMA_API_KEY",
                "models": ["qwen2.5-coder:7b"],
            }
        },
        "model_roles": {
            "coding_agent": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "proposal_composer": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "reward_judge": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "feedback_classifier": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "embedding": {"provider": "local", "model": "local_hash"},
        },
    },
    "cloud-coding-local-judges": {
        "label": "Cloud coding + local judges",
        "description": "Route coding to a cloud OpenAI-compatible provider while keeping low-cost judges local.",
        "model_providers": {
            "cloud": {
                "type": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "models": ["gpt-4.1"],
            },
            "local_ollama": {
                "type": "ollama",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "OLLAMA_API_KEY",
                "models": ["qwen2.5-coder:7b"],
            },
        },
        "model_roles": {
            "coding_agent": {"provider": "cloud", "model": "gpt-4.1"},
            "deep_project_pattern_mining": {"provider": "cloud", "model": "gpt-4.1"},
            "proposal_composer": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "reward_judge": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "feedback_classifier": {"provider": "local_ollama", "model": "qwen2.5-coder:7b"},
            "embedding": {"provider": "local", "model": "local_hash"},
        },
    },
}


class ModelService:
    def __init__(self, config: Config):
        self.config = config

    def providers(self) -> list[dict[str, Any]]:
        providers = self.config.get("model_providers", default={}) or {}
        result: list[dict[str, Any]] = []
        for provider_id, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            env_name = str(provider.get("api_key_env") or "")
            base_url = str(provider.get("base_url") or "")
            provider_type = str(provider.get("type") or "openai_compatible")
            local_endpoint = "localhost" in base_url or "127.0.0.1" in base_url or provider_type == "ollama"
            key_status = (
                "not_required"
                if local_endpoint and not os.environ.get(env_name)
                else ("configured" if env_name and os.environ.get(env_name) else "missing")
            )
            result.append(
                {
                    "provider_id": provider_id,
                    "type": provider_type,
                    "base_url": base_url,
                    "api_key_env": env_name or None,
                    "api_key_status": key_status,
                    "health": "unknown",
                    "health_checked_at": None,
                    "local_endpoint": local_endpoint,
                    "models": [item.get("name", item) if isinstance(item, dict) else item for item in provider.get("models", [])],
                    "timeout_seconds": provider.get("timeout_seconds"),
                }
            )
        if not result:
            result.append(
                {
                    "provider_id": "local",
                    "type": "local",
                    "base_url": None,
                    "api_key_env": None,
                    "api_key_status": "not_required",
                    "health": "ok",
                    "health_checked_at": None,
                    "local_endpoint": True,
                    "models": ["local_hash"],
                    "timeout_seconds": None,
                }
            )
        return result

    def roles(self) -> list[dict[str, Any]]:
        roles_config = self.config.get("model_roles", default={}) or {}
        providers = {item["provider_id"]: item for item in self.providers()}
        rows: list[dict[str, Any]] = []
        for role, category, mode, purpose in MODEL_ROLE_CATALOG:
            role_config = roles_config.get(role, {}) if isinstance(roles_config, dict) else {}
            if not isinstance(role_config, dict):
                role_config = {}
            provider = role_config.get("provider")
            model = role_config.get("model")
            enabled = role_config.get("enabled", True)
            fallback = role_config.get("fallback", [])
            status = "disabled" if enabled is False else "not_configured"
            health = "inactive"
            health_reason = "role is not configured"
            if provider == "local" and model == "local_hash":
                status = "connected"
                health = "ok"
                health_reason = "local_hash is available without a network endpoint"
            elif isinstance(provider, str) and isinstance(model, str) and provider and model:
                provider_row = providers.get(provider)
                if not provider_row:
                    status = "provider_missing"
                    health = "error"
                    health_reason = "configured provider is missing"
                elif provider_row.get("api_key_status") == "missing":
                    status = "missing_key"
                    health = "needs_secret"
                    health_reason = f"missing env var {provider_row.get('api_key_env')}"
                else:
                    status = "configured"
                    health = "unchecked"
                    health_reason = "route is configured but has not been tested in this request"
            rows.append(
                {
                    "role": role,
                    "category": category,
                    "purpose": purpose,
                    "mode": str(role_config.get("mode") or mode),
                    "provider": provider,
                    "model": model,
                    "fallback": fallback if isinstance(fallback, list) else [],
                    "status": status,
                    "health": health,
                    "health_reason": health_reason,
                }
            )
        return rows

    def check_routes(self, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        return ModelRouter(self.config).check_routes(timeout_seconds=timeout_seconds)

    def presets(self) -> list[dict[str, Any]]:
        return [
            {"preset_id": preset_id, "label": preset["label"], "description": preset["description"]}
            for preset_id, preset in SETUP_PRESETS.items()
        ]

    def apply_preset(self, preset_id: str, *, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise ServiceError(400, "`confirm` is required to apply a model preset")
        preset = SETUP_PRESETS.get(preset_id)
        if not preset:
            raise ServiceError(404, "Model preset not found")
        self.config.data["model_providers"] = copy.deepcopy(preset["model_providers"])
        self.config.data["model_roles"] = copy.deepcopy(preset["model_roles"])
        self.config.write()
        updated = Config.load(self.config.paths.root)
        return {
            "preset_id": preset_id,
            "providers": ModelService(updated).providers(),
            "roles": ModelService(updated).roles(),
        }

    def test_role(self, role: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        targets = self._role_route_targets(role)
        if not targets:
            row = next((item for item in self.roles() if item["role"] == role), None)
            return {"role": role, "routes": [], "status": row["status"] if row else "not_found", "detail": "No configured route target for this role"}
        router = ModelRouter(self.config)
        return {"role": role, "routes": [self._check_route_target(router, target, timeout_seconds=timeout_seconds) for target in targets]}

    def _role_route_targets(self, role: str) -> list[str]:
        roles = self.config.get("model_roles", default={}) or {}
        role_config = roles.get(role) if isinstance(roles, dict) else None
        if not isinstance(role_config, dict):
            return []
        targets: list[str] = []
        provider = role_config.get("provider")
        model = role_config.get("model")
        if isinstance(provider, str) and isinstance(model, str) and provider and model:
            targets.append(f"{provider}:{model}")
        for fallback in role_config.get("fallback") or []:
            if isinstance(fallback, str) and ":" in fallback:
                targets.append(fallback)
            elif isinstance(fallback, dict):
                fallback_provider = fallback.get("provider")
                fallback_model = fallback.get("model")
                if isinstance(fallback_provider, str) and isinstance(fallback_model, str):
                    targets.append(f"{fallback_provider}:{fallback_model}")
        return list(dict.fromkeys(targets))

    def _check_route_target(self, router: ModelRouter, target: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        provider_name, model = target.split(":", 1) if ":" in target else ("", target)
        started = time.monotonic()
        result: dict[str, Any] = {
            "target": target,
            "provider": provider_name,
            "model": model,
            "provider_known": provider_name in router.providers or provider_name == "local",
            "timeout_seconds": timeout_seconds,
        }
        if provider_name == "local" and model == "local_hash":
            return {**result, "status": "ok", "health": "ok", "detail": "local_hash is available", "latency_ms": int((time.monotonic() - started) * 1000)}
        provider = router.providers.get(provider_name)
        if provider is None:
            return {**result, "status": "error", "health": "error", "detail": f"unknown provider: {provider_name or '(missing)'}", "latency_ms": int((time.monotonic() - started) * 1000)}
        try:
            provider.chat(
                {
                    "model": model,
                    "messages": [{"role": "system", "content": "Reply OK."}, {"role": "user", "content": "OK?"}],
                    "temperature": 0,
                    "max_tokens": 8,
                    "timeout": timeout_seconds,
                }
            )
            status, detail = "ok", "model endpoint accepted a minimal chat request"
        except ModelUnavailable as exc:
            status, detail = "unavailable", str(exc)
        except ModelError as exc:
            status, detail = "error", str(exc)
        except Exception as exc:
            status, detail = "error", f"{exc.__class__.__name__}: {exc}"
        return {**result, "status": status, "health": "ok" if status == "ok" else "error", "detail": detail, "latency_ms": int((time.monotonic() - started) * 1000)}
