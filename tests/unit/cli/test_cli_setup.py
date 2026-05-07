from __future__ import annotations

from pathlib import Path

from praxile.cli import main
from praxile.config import Config


def test_setup_configures_ollama_non_interactively(tmp_path: Path):
    assert main(["--project", str(tmp_path), "init", "--no-detect"]) == 0

    exit_code = main(
        [
            "--project",
            str(tmp_path),
            "setup",
            "--provider",
            "ollama",
            "--model",
            "qwen-test",
            "--base-url",
            "http://localhost:11434/v1",
            "--api-key-env",
            "OLLAMA_API_KEY",
            "--channel",
            "none",
        ]
    )

    assert exit_code == 0
    config = Config.load(tmp_path)
    assert config.get("model_providers", "local_ollama", "type") == "ollama"
    assert config.get("model_roles", "coding_agent", "model") == "qwen-test"
    assert config.get("routing", "coding_model") == "local_ollama:qwen-test"


def test_setup_none_keeps_clean_model_configuration(tmp_path: Path):
    assert main(["--project", str(tmp_path), "init", "--no-detect"]) == 0
    assert main(["--project", str(tmp_path), "setup", "--provider", "none", "--channel", "none"]) == 0

    config = Config.load(tmp_path)
    assert config.get("model_providers", default={}) == {}
    assert config.get("model_roles", default={}) == {"embedding": {"provider": "local", "model": "local_hash"}}
