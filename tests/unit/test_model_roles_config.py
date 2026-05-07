import pytest
from praxile.config import Config
from praxile.model import ModelRouter
from pathlib import Path
import tempfile

def test_model_roles_config_and_router():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.load(Path(tmp))
        
        # Manually inject some routing config
        config.data["routing"] = {
            "default_model": "openai:gpt-3.5",
            "coding_model": "openai:gpt-4",
            "planning_model": "openai:gpt-4o-mini",
            "evolution_model": "ollama:qwen",
            "cheap_model": "ollama:llama3",
            "private_model": "ollama:deepseek"
        }
        
        router = ModelRouter(config)
        
        # Test routing fallback logic
        _, target = router._select_route_target("coding", high_risk=True)
        assert target == "openai:gpt-4"
        
        _, target = router._select_route_target("evolution")
        assert target == "ollama:qwen"
        
        _, target = router._select_route_target("planning")
        assert target == "openai:gpt-4o-mini"
        
        _, target = router._select_route_target("coding", private=True)
        assert target == "ollama:deepseek"

def test_model_roles_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        config = Config.load(Path(tmp))
        config.data["routing"] = {
            "default_model": "openai:gpt-4"
            # leaving planning_model, evolution_model empty
        }
        
        router = ModelRouter(config)
        
        _, target = router._select_route_target("evolution")
        assert target == "openai:gpt-4"
        
        _, target = router._select_route_target("planning")
        assert target == "openai:gpt-4"
