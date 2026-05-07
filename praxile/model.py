from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .config import Config
from .http_transport import HTTPResponseDecodeError, HTTPTransport, HTTPTransportError, make_transport


class ModelError(RuntimeError):
    pass


class ModelUnavailable(ModelError):
    pass


DEFAULT_MODEL_TIMEOUT_SECONDS = 30
DEFAULT_ONLINE_CHECK_TIMEOUT_SECONDS = 8
ROLE_PURPOSE_ALIASES = {
    "default": "coding_agent",
    "coding": "coding_agent",
    "coding_agent": "coding_agent",
    "planning": "coding_agent",
    "evolution": "experience_reflection",
    "evolution_model": "experience_reflection",
    "experience": "experience_reflection",
    "experience_reflection": "experience_reflection",
    "evidence": "evidence_extraction",
    "evidence_extraction": "evidence_extraction",
    "deep_project_pattern_mining": "deep_project_pattern_mining",
    "reward": "reward_judge",
    "reward_judge": "reward_judge",
    "proposal": "proposal_composer",
    "proposal_composer": "proposal_composer",
    "review": "review_recommendation",
    "review_recommendation": "review_recommendation",
    "feedback": "feedback_classifier",
    "feedback_classifier": "feedback_classifier",
    "attribution": "attribution_judge",
    "attribution_judge": "attribution_judge",
    "counterexample": "counterexample_checker",
    "counterexample_checker": "counterexample_checker",
    "pattern_mining": "pattern_mining",
    "project_pattern": "project_pattern_composer",
    "project_pattern_composer": "project_pattern_composer",
    "cheap_reasoner": "cheap_reasoner",
    "cheap": "review_recommendation",
    "cheap_model": "review_recommendation",
}
LEGACY_ROUTE_FOR_ROLE = {
    "coding_agent": "coding_model",
    "experience_reflection": "evolution_model",
    "evidence_extraction": "evolution_model",
    "deep_project_pattern_mining": "evolution_model",
    "reward_judge": "cheap_model",
    "proposal_composer": "evolution_model",
    "review_recommendation": "cheap_model",
    "cheap_reasoner": "cheap_model",
    "feedback_classifier": "cheap_model",
    "attribution_judge": "cheap_model",
    "counterexample_checker": "evolution_model",
    "pattern_mining": "evolution_model",
    "project_pattern_composer": "evolution_model",
}


def _timeout_seconds(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, timeout)


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    name: str
    role: str = "default"
    context_window: int = 0
    supports_tools: bool = False
    supports_vision: bool = False


class ModelProvider(Protocol):
    name: str

    def list_models(self) -> list[ModelInfo]:
        ...

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        ...

    def supports_tools(self, model: str) -> bool:
        ...

    def max_context_window(self, model: str) -> int:
        ...


def _model_info_from_config(provider: str, item: Any, *, default_name: str = "unknown") -> ModelInfo:
    if isinstance(item, str):
        return ModelInfo(provider=provider, name=item)
    if not isinstance(item, dict):
        return ModelInfo(provider=provider, name=default_name)
    return ModelInfo(
        provider=provider,
        name=str(item.get("name", default_name)),
        role=str(item.get("role", "default")),
        context_window=int(item.get("context_window", 0) or 0),
        supports_tools=bool(item.get("supports_tools", False)),
        supports_vision=bool(item.get("supports_vision", False)),
    )


class OpenAICompatibleProvider:
    def __init__(self, name: str, config: dict[str, Any], transport: HTTPTransport | None = None):
        self.name = name
        self.config = config
        self.base_url = config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.api_key_env = config.get("api_key_env", "OPENAI_API_KEY")
        self.timeout_seconds = _timeout_seconds(config.get("timeout_seconds"), DEFAULT_MODEL_TIMEOUT_SECONDS)
        self.transport = transport or make_transport(config)
        self.models = [_model_info_from_config(name, item) for item in config.get("models", [])]

    def list_models(self) -> list[ModelInfo]:
        return self.models

    def _api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key()
        if not api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise ModelUnavailable(f"Missing API key env var: {self.api_key_env}")
        body = {
            "model": request["model"],
            "messages": request["messages"],
            "temperature": request.get("temperature", 0.2),
        }
        if request.get("max_tokens"):
            body["max_tokens"] = request["max_tokens"]
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = f"{self.base_url}/chat/completions"
        try:
            payload = self.transport.post_json(
                url,
                headers=headers,
                payload=body,
                timeout=_timeout_seconds(request.get("timeout"), self.timeout_seconds),
            )
        except HTTPResponseDecodeError as exc:
            raise ModelError(f"Model returned invalid JSON envelope: {exc}") from exc
        except HTTPTransportError as exc:
            raise ModelUnavailable(str(exc)) from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"Unexpected OpenAI-compatible response shape: {payload}") from exc
        return {
            "content": content,
            "raw": payload,
            "usage": payload.get("usage", {}),
            "provider": self.name,
            "model": request["model"],
        }

    def supports_tools(self, model: str) -> bool:
        return any(item.name == model and item.supports_tools for item in self.models)

    def max_context_window(self, model: str) -> int:
        for item in self.models:
            if item.name == model:
                return item.context_window
        return 0


class AnthropicProvider:
    def __init__(self, name: str, config: dict[str, Any], transport: HTTPTransport | None = None):
        self.name = name
        self.config = config
        self.api_key_env = config.get("api_key_env", "ANTHROPIC_API_KEY")
        self.base_url = config.get("base_url", "https://api.anthropic.com/v1").rstrip("/")
        self.timeout_seconds = _timeout_seconds(config.get("timeout_seconds"), DEFAULT_MODEL_TIMEOUT_SECONDS)
        self.transport = transport or make_transport(config)
        self.models = [_model_info_from_config(name, item, default_name="claude-sonnet") for item in config.get("models", [])]

    def list_models(self) -> list[ModelInfo]:
        return self.models

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ModelUnavailable(f"Missing API key env var: {self.api_key_env}")
        messages = request["messages"]
        system_parts = [m["content"] for m in messages if m.get("role") == "system"]
        user_messages = [m for m in messages if m.get("role") != "system"]
        body = {
            "model": request["model"],
            "max_tokens": request.get("max_tokens", 4096),
            "temperature": request.get("temperature", 0.2),
            "system": "\n\n".join(system_parts),
            "messages": user_messages,
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            payload = self.transport.post_json(
                f"{self.base_url}/messages",
                headers=headers,
                payload=body,
                timeout=_timeout_seconds(request.get("timeout"), self.timeout_seconds),
            )
        except HTTPResponseDecodeError as exc:
            raise ModelError(f"Model returned invalid JSON envelope: {exc}") from exc
        except HTTPTransportError as exc:
            raise ModelUnavailable(str(exc)) from exc
        content_blocks = payload.get("content", [])
        content = "\n".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")
        return {
            "content": content,
            "raw": payload,
            "usage": payload.get("usage", {}),
            "provider": self.name,
            "model": request["model"],
        }

    def supports_tools(self, model: str) -> bool:
        return any(item.name == model and item.supports_tools for item in self.models)

    def max_context_window(self, model: str) -> int:
        for item in self.models:
            if item.name == model:
                return item.context_window
        return 0


class ModelRouter:
    def __init__(self, config: Config):
        self.config = config
        self.providers = self._build_providers()

    def _build_providers(self) -> dict[str, ModelProvider]:
        providers: dict[str, ModelProvider] = {}
        transport_config = self.config.get("model", default={}) or {}
        for name, provider_config in self.config.get("model_providers", default={}).items():
            merged_config = dict(transport_config)
            merged_config.update(provider_config)
            provider_type = provider_config.get("type", "openai_compatible")
            if provider_type in {"openai", "openai_compatible"}:
                providers[name] = OpenAICompatibleProvider(name, merged_config)
            elif provider_type == "ollama":
                merged_config.setdefault("base_url", "http://localhost:11434/v1")
                merged_config.setdefault("api_key_env", "OLLAMA_API_KEY")
                providers[name] = OpenAICompatibleProvider(name, merged_config)
            elif provider_type == "anthropic":
                providers[name] = AnthropicProvider(name, merged_config)
        return providers

    def list_models(self) -> list[ModelInfo]:
        result: list[ModelInfo] = []
        for provider in self.providers.values():
            result.extend(provider.list_models())
        return result

    def _runtime_timeout_seconds(self) -> int:
        return _timeout_seconds(
            self.config.get("runtime", "model_timeout_seconds", default=None),
            DEFAULT_MODEL_TIMEOUT_SECONDS,
        )

    def _online_check_timeout_seconds(self) -> int:
        return _timeout_seconds(
            self.config.get("runtime", "online_check_timeout_seconds", default=None),
            DEFAULT_ONLINE_CHECK_TIMEOUT_SECONDS,
        )

    def _select_route_target(
        self,
        purpose: str = "default",
        *,
        private: bool = False,
        high_risk: bool = False,
    ) -> tuple[str, str | None]:
        role_name = self._role_name_for_purpose(purpose)
        if private:
            private_target = self.config.get("routing", "private_model", default=None)
            if private_target:
                return "private_model", private_target
        legacy_purpose = str(purpose or "default") in {"default", "coding", "planning", "evolution", "cheap"}
        if not legacy_purpose:
            role_target = self._role_primary_target(role_name)
            if role_target:
                return f"model_roles.{role_name}", role_target
        routing = self.config.get("routing", default={})
        if legacy_purpose:
            if purpose == "default":
                route_key = "default_model"
            elif purpose == "cheap":
                route_key = "cheap_model"
            else:
                route_key = f"{purpose}_model"
        else:
            route_key = LEGACY_ROUTE_FOR_ROLE.get(role_name, f"{purpose}_model")
        
        if purpose == "coding" and high_risk:
            route_key = "coding_model"
        elif purpose == "default":
            route_key = "default_model"

        target = routing.get(route_key)
        if not target:
            # Fallbacks for the new roles if they are not explicitly configured
            if purpose == "evolution":
                target = routing.get("cheap_model") or routing.get("default_model")
            elif purpose == "planning":
                target = routing.get("default_model")
            elif purpose == "coding":
                target = routing.get("default_model")
            else:
                target = routing.get("default_model")
                
        return route_key, target

    def _role_name_for_purpose(self, purpose: str) -> str:
        normalized = str(purpose or "default").strip()
        if normalized.startswith("model_roles."):
            normalized = normalized.split(".", 1)[1]
        return ROLE_PURPOSE_ALIASES.get(normalized, normalized)

    def _role_primary_target(self, role_name: str) -> str | None:
        roles = self.config.get("model_roles", default={})
        if not isinstance(roles, dict):
            return None
        role = roles.get(role_name)
        if not isinstance(role, dict):
            return None
        provider = role.get("provider")
        model = role.get("model")
        if provider == "local" and model == "local_hash":
            return None
        if isinstance(provider, str) and isinstance(model, str) and provider and model:
            return f"{provider}:{model}"
        return None

    def route(self, purpose: str = "default", *, private: bool = False, high_risk: bool = False) -> tuple[ModelProvider, str]:
        _route_key, target = self._select_route_target(purpose, private=private, high_risk=high_risk)
        return self._provider_for_target(target, purpose=purpose)

    def _provider_for_target(self, target: str | None, *, purpose: str = "default") -> tuple[ModelProvider, str]:
        if not target or ":" not in target:
            raise ModelUnavailable(f"No route configured for purpose={purpose}")
        provider_name, model = target.split(":", 1)
        provider = self.providers.get(provider_name)
        if provider is None:
            raise ModelUnavailable(f"Unknown provider in route: {provider_name}")
        return provider, model

    def describe_route(self, purpose: str = "default", *, private: bool = False, high_risk: bool = False) -> dict[str, Any]:
        route_key, target = self._select_route_target(purpose, private=private, high_risk=high_risk)
        provider_name, model = target.split(":", 1) if target and ":" in target else ("", target or "")
        role_name = self._role_name_for_purpose(purpose)
        reasons: list[str] = []
        if private:
            reasons.append("privacy-sensitive task routed to private/local model when configured")
        if high_risk:
            reasons.append("high-risk task requires stronger model route and human approval")
        if role_name == "coding_agent":
            reasons.append("code implementation uses coding_agent model role")
        elif role_name in {"experience_reflection", "evidence_extraction", "proposal_composer", "project_pattern_composer"}:
            reasons.append("experience extraction uses experience_reflection/proposal model role")
        elif role_name in {"feedback_classifier", "review_recommendation", "cheap_reasoner"}:
            reasons.append("feedback and review classification uses a cheap/local model role when configured")
        elif role_name == "attribution_judge":
            reasons.append("asset attribution judgement uses a cheap/local semantic model role when configured")
        elif role_name in {"counterexample_checker", "pattern_mining", "deep_project_pattern_mining"}:
            reasons.append("pattern mining and counterexample checks use an evolution/reflection model role when configured")
        else:
            reasons.append(f"purpose `{purpose}` resolved through model roles when configured")
        return {
            "purpose": purpose,
            "model_role": self._role_name_for_purpose(purpose),
            "private": private,
            "high_risk": high_risk,
            "route_key": route_key,
            "provider": provider_name,
            "model": model,
            "target": target,
            "reasons": reasons,
        }

    def _route_candidates(
        self,
        purpose: str = "default",
        *,
        private: bool = False,
        high_risk: bool = False,
    ) -> list[dict[str, Any]]:
        primary_key, primary_target = self._select_route_target(purpose, private=private, high_risk=high_risk)
        routing = self.config.get("routing", default={})
        fallback_map = routing.get("fallbacks", {}) if isinstance(routing, dict) else {}
        raw_fallbacks = self._role_fallback_targets(self._role_name_for_purpose(purpose))
        if not raw_fallbacks and isinstance(fallback_map, dict):
            raw_fallbacks = fallback_map.get(primary_key, [])
        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(route_key: str, target: str | None, *, fallback: bool) -> None:
            if not target or target in seen:
                return
            seen.add(target)
            provider_name, model = target.split(":", 1) if ":" in target else ("", target)
            candidates.append(
                {
                    "route_key": route_key,
                    "target": target,
                    "provider": provider_name,
                    "model": model,
                    "fallback": fallback,
                }
            )

        add(primary_key, primary_target, fallback=False)
        for item in raw_fallbacks:
            if isinstance(item, dict):
                provider = item.get("provider")
                model = item.get("model")
                target = f"{provider}:{model}" if isinstance(provider, str) and isinstance(model, str) else None
                add(f"model_roles.{self._role_name_for_purpose(purpose)}.fallback", target, fallback=True)
                continue
            if isinstance(item, str):
                target = routing.get(item) if ":" not in item and isinstance(routing, dict) else item
                add(item, target, fallback=True)
        return candidates

    def _role_fallback_targets(self, role_name: str) -> list[Any]:
        roles = self.config.get("model_roles", default={})
        if not isinstance(roles, dict):
            return []
        role = roles.get(role_name)
        if not isinstance(role, dict):
            return []
        fallback = role.get("fallback", [])
        return fallback if isinstance(fallback, list) else []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        purpose: str = "default",
        private: bool = False,
        high_risk: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        primary_route = self.describe_route(purpose, private=private, high_risk=high_risk)
        timeout_seconds = _timeout_seconds(timeout, self._runtime_timeout_seconds())
        attempts: list[dict[str, Any]] = []
        last_unavailable: ModelUnavailable | None = None
        backoff = float(self.config.get("routing", "fallback_backoff_seconds", default=0.25) or 0.0)
        candidates = self._route_candidates(purpose, private=private, high_risk=high_risk)
        for index, candidate in enumerate(candidates):
            started = time.monotonic()
            try:
                provider, model = self._provider_for_target(candidate["target"], purpose=purpose)
                response = provider.chat(
                    {
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "timeout": timeout_seconds,
                    }
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                attempts.append({**candidate, "status": "success", "latency_ms": latency_ms})
                route = {
                    **primary_route,
                    "route_key": candidate["route_key"],
                    "provider": candidate["provider"],
                    "model": candidate["model"],
                    "target": candidate["target"],
                    "fallback_used": bool(candidate["fallback"]),
                    "fallback_attempts": attempts,
                }
                response["route"] = route
                response["latency_ms"] = latency_ms
                return response
            except ModelUnavailable as exc:
                latency_ms = int((time.monotonic() - started) * 1000)
                attempts.append(
                    {
                        **candidate,
                        "status": "unavailable",
                        "latency_ms": latency_ms,
                        "error": str(exc),
                    }
                )
                last_unavailable = exc
                if index < len(candidates) - 1 and backoff > 0:
                    time.sleep(backoff * (index + 1))
                continue
        detail = "; ".join(f"{item['target']}={item['status']}" for item in attempts) or "no route candidates"
        if last_unavailable:
            raise ModelUnavailable(f"All model routes unavailable ({detail}); last error: {last_unavailable}") from last_unavailable
        raise ModelUnavailable(f"All model routes unavailable ({detail})")

    def configured_route_targets(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[str]] = {}
        for key, value in self.config.get("routing", default={}).items():
            if isinstance(value, str):
                grouped.setdefault(value, []).append(key)
        for role_name, role in self.config.get("model_roles", default={}).items():
            if not isinstance(role, dict):
                continue
            target = self._role_primary_target(str(role_name))
            if target:
                grouped.setdefault(target, []).append(f"model_roles.{role_name}")
            for fallback in self._role_fallback_targets(str(role_name)):
                if isinstance(fallback, dict):
                    provider = fallback.get("provider")
                    model = fallback.get("model")
                    if isinstance(provider, str) and isinstance(model, str):
                        grouped.setdefault(f"{provider}:{model}", []).append(f"model_roles.{role_name}.fallback")
                elif isinstance(fallback, str) and ":" in fallback:
                    grouped.setdefault(fallback, []).append(f"model_roles.{role_name}.fallback")
        routes: list[dict[str, Any]] = []
        for target, route_keys in grouped.items():
            provider_name, model = target.split(":", 1) if ":" in target else ("", target)
            routes.append(
                {
                    "route_keys": route_keys,
                    "target": target,
                    "provider": provider_name,
                    "model": model,
                    "provider_known": provider_name in self.providers,
                }
            )
        return routes

    def check_routes(self, timeout_seconds: int | None = None) -> list[dict[str, Any]]:
        timeout = _timeout_seconds(timeout_seconds, self._online_check_timeout_seconds())
        results: list[dict[str, Any]] = []
        for route in self.configured_route_targets():
            started = time.monotonic()
            status = "ok"
            detail = "model endpoint accepted a minimal chat request"
            provider = self.providers.get(route["provider"])
            if provider is None:
                status = "error"
                detail = f"unknown provider: {route['provider'] or '(missing)'}"
            elif not route["model"]:
                status = "error"
                detail = "missing model name"
            else:
                try:
                    provider.chat(
                        {
                            "model": route["model"],
                            "messages": [
                                {"role": "system", "content": "Reply with OK only."},
                                {"role": "user", "content": "OK?"},
                            ],
                            "temperature": 0,
                            "max_tokens": 8,
                            "timeout": timeout,
                        }
                    )
                except ModelUnavailable as exc:
                    status = "unavailable"
                    detail = str(exc)
                except ModelError as exc:
                    status = "error"
                    detail = str(exc)
                except Exception as exc:  # pragma: no cover - defensive network envelope guard
                    status = "error"
                    detail = f"{exc.__class__.__name__}: {exc}"
            result = dict(route)
            result.update(
                {
                    "status": status,
                    "detail": detail,
                    "timeout_seconds": timeout,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                }
            )
            results.append(result)
        return results
