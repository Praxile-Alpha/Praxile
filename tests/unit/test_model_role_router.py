from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from praxile.config import Config, ConfigValidationError, validate_config
from praxile.model import ModelRequestCancelled, ModelRouter, ModelUnavailable


class FailingProvider:
    name = "failing"

    def list_models(self):
        return []

    def chat(self, request):
        raise ModelUnavailable("primary down")

    def supports_tools(self, model):
        return False

    def max_context_window(self, model):
        return 0


class EchoProvider:
    name = "echo"

    def list_models(self):
        return []

    def chat(self, request):
        return {
            "content": "{\"type\":\"finish\",\"status\":\"completed\",\"summary\":\"ok\"}",
            "usage": {},
            "provider": "echo",
            "model": request["model"],
        }

    def supports_tools(self, model):
        return False

    def max_context_window(self, model):
        return 0


class BlockingProvider:
    name = "blocking"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.transport = self
        self.closed = False

    def list_models(self):
        return []

    def chat(self, request):
        self.started.set()
        self.release.wait(timeout=5)
        return {
            "content": "{\"type\":\"finish\",\"status\":\"completed\",\"summary\":\"late\"}",
            "usage": {},
            "provider": "blocking",
            "model": request["model"],
        }

    def close(self):
        self.closed = True
        self.release.set()

    def supports_tools(self, model):
        return False

    def max_context_window(self, model):
        return 0


def test_model_roles_select_primary_and_fallback(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {
        "primary": {
            "type": "openai_compatible",
            "base_url": "http://localhost:1/v1",
            "models": [{"name": "strong"}],
        },
        "local": {
            "type": "ollama",
            "base_url": "http://localhost:11434/v1",
            "models": [{"name": "qwen"}],
        },
    }
    config.data["model_roles"] = {
        "coding_agent": {
            "provider": "primary",
            "model": "strong",
            "fallback": [{"provider": "local", "model": "qwen"}],
        }
    }
    router = ModelRouter(config)
    router.providers = {"primary": FailingProvider(), "local": EchoProvider()}

    route = router.describe_route("coding_agent")
    assert route["route_key"] == "model_roles.coding_agent"
    assert route["target"] == "primary:strong"

    response = router.chat([{"role": "user", "content": "hi"}], purpose="coding_agent")
    assert response["provider"] == "echo"
    assert response["route"]["fallback_used"] is True
    assert [item["target"] for item in response["route"]["fallback_attempts"]] == ["primary:strong", "local:qwen"]


def test_model_route_can_cancel_in_flight_provider_request(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {
        "local": {
            "type": "ollama",
            "base_url": "http://localhost:11434/v1",
            "models": [{"name": "qwen"}],
        }
    }
    config.data["model_roles"] = {"coding_agent": {"provider": "local", "model": "qwen"}}
    router = ModelRouter(config)
    provider = BlockingProvider()
    router.providers = {"local": provider}

    started_at = time.monotonic()
    with pytest.raises(ModelRequestCancelled):
        router.chat(
            [{"role": "user", "content": "hi"}],
            purpose="coding_agent",
            cancel_requested=lambda: provider.started.is_set(),
        )
    elapsed = time.monotonic() - started_at

    assert elapsed < 1.0
    assert provider.closed is True


def test_model_role_validation_checks_declared_models(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {
        "local": {
            "type": "ollama",
            "base_url": "http://localhost:11434/v1",
            "models": [{"name": "qwen"}],
        }
    }
    config.data["model_roles"] = {
        "reward_judge": {
            "provider": "local",
            "model": "missing",
        }
    }
    with pytest.raises(ConfigValidationError, match="model_roles.reward_judge.model"):
        validate_config(config.data)


def test_new_experience_quality_roles_route_independently(tmp_path: Path):
    config = Config.load(tmp_path)
    config.data["model_providers"] = {
        "local": {
            "type": "ollama",
            "base_url": "http://localhost:11434/v1",
            "models": [{"name": "qwen"}],
        }
    }
    config.data["model_roles"] = {
        "cheap_reasoner": {"provider": "local", "model": "qwen"},
        "feedback_classifier": {"provider": "local", "model": "qwen"},
        "attribution_judge": {"provider": "local", "model": "qwen"},
        "counterexample_checker": {"provider": "local", "model": "qwen"},
        "pattern_mining": {"provider": "local", "model": "qwen"},
        "project_pattern_composer": {"provider": "local", "model": "qwen"},
    }
    router = ModelRouter(config)

    assert router.describe_route("cheap_reasoner")["route_key"] == "model_roles.cheap_reasoner"
    assert router.describe_route("feedback_classifier")["route_key"] == "model_roles.feedback_classifier"
    assert router.describe_route("attribution_judge")["route_key"] == "model_roles.attribution_judge"
    assert router.describe_route("counterexample_checker")["route_key"] == "model_roles.counterexample_checker"
    assert router.describe_route("pattern_mining")["route_key"] == "model_roles.pattern_mining"
    assert router.describe_route("project_pattern_composer")["route_key"] == "model_roles.project_pattern_composer"
