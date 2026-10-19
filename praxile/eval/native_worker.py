"""Fresh-state native executor for externally owned benchmark containers.

This module executes public instructions, never official evaluators. The caller
must isolate private scoring materials and constrain container/network access.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import Config, ProjectPaths, default_config, validate_config
from ..runtime import AgentRuntime


def benchmark_config(root: Path, *, model: str, base_url: str, max_steps: int) -> Config:
    root = root.resolve(strict=True)
    if not root.is_dir() or (root / ".praxile").exists() or (root / ".praxile").is_symlink():
        raise ValueError("native benchmark worker requires a workspace without .praxile state")
    if not model.strip() or not 1 <= max_steps <= 1000:
        raise ValueError("model and step budget must be explicitly configured")
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("base_url must be an HTTP endpoint without embedded credentials or query")
    data = default_config(root)
    data["runtime"]["max_steps"] = max_steps
    data["model"]["transport"] = "urllib"
    data["model_providers"] = {"benchmark": {
        "type": "openai_compatible", "base_url": base_url,
        "api_key_env": "PRAXILE_BENCHMARK_API_KEY",
        "models": [{"name": model, "role": "strong_coding", "supports_tools": False}],
    }}
    data["model_roles"]["coding_agent"] = {"provider": "benchmark", "model": model}
    validate_config(data)
    return Config(data, ProjectPaths(root))


def run_native(root: Path, instruction: str, *, model: str, base_url: str, max_steps: int) -> dict[str, Any]:
    if not instruction.strip():
        raise ValueError("public instruction is empty")
    config = benchmark_config(root, model=model, base_url=base_url, max_steps=max_steps)
    runtime = AgentRuntime(config)
    try:
        trajectory = runtime.run(instruction, max_steps=max_steps, use_experience=False)
    finally:
        # run closes tools; providers also own persistent HTTP transports.
        for provider in runtime.router.providers.values():
            close = getattr(getattr(provider, "transport", None), "close", None)
            if close:
                close()
    return {"schema_version": "praxile.native_benchmark_run.v1", "trajectory": trajectory,
            "official_evaluation": "not_run", "experience_mode": "fresh_disabled"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--instruction", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    args = parser.parse_args()
    result = run_native(args.workspace, args.instruction.read_text(encoding="utf-8"),
                        model=args.model, base_url=args.base_url, max_steps=args.max_steps)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.result.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(args.result)


if __name__ == "__main__":
    main()
