"""Fresh-state native executor for externally owned benchmark containers.

This module executes public instructions, never official evaluators. The caller
must isolate private scoring materials and constrain container/network access.
"""
from __future__ import annotations

import argparse
import json
import os
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..config import Config, ProjectPaths, default_config, validate_config
from ..runtime import AgentRuntime
from ..task_input import TaskInput


def load_task_input(root: Path, instruction: str, relative_path: str, expected_sha256: str) -> TaskInput:
    """Read only a controller-selected public file before starting model execution."""
    path = Path(relative_path)
    root = root.resolve(strict=True)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("task body must be workspace-relative")
    target = root / path
    if target.resolve(strict=True) != target or not target.is_file():
        raise ValueError("task body must be a regular file without symlink components")
    with target.open("rb") as handle:
        raw = handle.read(262145)
    if len(raw) > 262144 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("task body size or digest mismatch")
    return TaskInput(raw.decode("utf-8"), instruction, relative_path)


def benchmark_config(root: Path, *, model: str, base_url: str, max_steps: int, model_timeout_seconds: int = 30,
                     renderer_executable: str = "") -> Config:
    root = root.resolve(strict=True)
    if not root.is_dir() or (root / ".praxile").exists() or (root / ".praxile").is_symlink():
        raise ValueError("native benchmark worker requires a workspace without .praxile state")
    if not model.strip() or not 1 <= max_steps <= 1000:
        raise ValueError("model and step budget must be explicitly configured")
    url = urlsplit(base_url)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("base_url must be an HTTP endpoint without embedded credentials or query")
    data = default_config(root)
    data["documents"]["renderer_executable"] = renderer_executable
    if type(model_timeout_seconds) is not int or not 1 <= model_timeout_seconds <= 110:
        raise ValueError("model_timeout_seconds must be an integer in [1, 110]")
    data["runtime"]["model_timeout_seconds"] = model_timeout_seconds
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


def run_native(root: Path, instruction: str | TaskInput, *, model: str, base_url: str, max_steps: int,
               model_timeout_seconds: int = 30, output_directory: str | None = None,
               renderer_executable: str = "") -> dict[str, Any]:
    if isinstance(instruction, str) and not instruction.strip():
        raise ValueError("public instruction is empty")
    if output_directory is not None:
        path = Path(output_directory)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("output directory must be workspace-relative")
    config = benchmark_config(root, model=model, base_url=base_url, max_steps=max_steps,
                              model_timeout_seconds=model_timeout_seconds, renderer_executable=renderer_executable)
    runtime = AgentRuntime(config)
    try:
        trajectory = runtime.run(instruction, max_steps=max_steps, use_experience=False)
    finally:
        # run closes tools; providers also own persistent HTTP transports.
        for provider in runtime.router.providers.values():
            close = getattr(getattr(provider, "transport", None), "close", None)
            if close:
                close()
    result = {"schema_version": "praxile.native_benchmark_run.v1", "trajectory": trajectory,
              "official_evaluation": "not_run", "experience_mode": "fresh_disabled"}
    if output_directory is not None:
        from ..artifact_inventory import inspect_outputs
        result["artifact_inventory"] = inspect_outputs(root, output_directory)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--instruction", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--model-timeout-seconds", type=int, default=30)
    parser.add_argument("--output-directory")
    parser.add_argument("--renderer-executable", default="")
    parser.add_argument("--capability-file", type=Path)
    parser.add_argument("--task-body-file")
    parser.add_argument("--task-body-sha256")
    args = parser.parse_args()
    if bool(args.task_body_file) != bool(args.task_body_sha256):
        parser.error("task body file and digest must be configured together")
    instruction = args.instruction.read_text(encoding="utf-8")
    if args.task_body_file:
        instruction = load_task_input(args.workspace, instruction, args.task_body_file, args.task_body_sha256)
    if args.capability_file is not None:
        from .credential_broker import read_private_key
        capability = read_private_key(args.capability_file)
        args.capability_file.unlink()
        os.environ["PRAXILE_BENCHMARK_API_KEY"] = capability
    result = run_native(args.workspace, instruction,
                        model=args.model, base_url=args.base_url, max_steps=args.max_steps,
                        model_timeout_seconds=args.model_timeout_seconds, output_directory=args.output_directory,
                        renderer_executable=args.renderer_executable)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.result.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temporary.replace(args.result)


if __name__ == "__main__":
    main()
