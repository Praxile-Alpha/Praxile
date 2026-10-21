"""Explicit, bounded role probes without benchmark data or routing fallback."""
import os
import time

from ..model import ModelRouter
from ..utils import utc_now


def inspect_model_roles(config, *, probe=False):
    roles = ("coding_agent", "content_review_judge")
    results = []
    router = ModelRouter(config) if probe else None
    try:
        for role in roles:
            binding = config.get("model_roles", role, default={})
            binding = binding if isinstance(binding, dict) else {}
            provider_name, model = binding.get("provider"), binding.get("model")
            provider_config = config.get("model_providers", provider_name, default={}) if provider_name else {}
            check = {"role": role, "provider": provider_name, "model": model,
                     "status": "not_configured", "probe_attempted": False}
            results.append(check)
            if not provider_name or not model or not provider_config:
                continue
            env_name = provider_config.get("api_key_env")
            check["credential_present"] = bool(os.environ.get(env_name)) if env_name else None
            check["status"] = "configured_unverified"
            if not probe:
                continue
            provider = router.providers.get(provider_name)
            if provider is None:
                check["status"] = "unknown_provider"
                continue
            started = time.monotonic()
            check.update(probe_attempted=True, timeout_seconds=8, max_output_tokens=32)
            try:
                response = provider.chat({"model": model,
                    "messages": [{"role": "user", "content": "Reply with OK only."}],
                    "max_tokens": 32, "timeout": 8,
                    "request_options": binding.get("request_options", {})})
                check["status"] = "reachable" if (isinstance(response, dict)
                    and isinstance(response.get("content"), str) and response["content"].strip()) else "invalid_response"
            except Exception as error:
                check.update(status="unavailable", error_type=type(error).__name__)
            finally:
                check["latency_ms"] = int((time.monotonic() - started) * 1000)
    finally:
        if router is not None:
            for provider in router.providers.values():
                close = getattr(getattr(provider, "transport", None), "close", None)
                if close:
                    close()
    return {"roles": results, "observed_at": utc_now(),
            "models_reachable": all(item["status"] == "reachable" for item in results),
            "official_judge_verified": False,
            "note": "Explicit primary roles only; no fallback, task data, rubric, or quality validation. "
                    "Reachability does not establish judge independence or capability."}
