from __future__ import annotations

from importlib.util import find_spec
from typing import Any

from .identity import agent_manifest


HERMES_CAPABILITY_MODULES: dict[str, list[str]] = {
    "providers": [
        "hermes_cli.providers",
        "hermes_cli.model_switch",
        "agent.transports.chat_completions",
        "agent.transports.anthropic",
    ],
    "runtime": [
        "agent.context_engine",
        "agent.prompt_builder",
        "hermes_cli.oneshot",
    ],
    "tools_terminal": [
        "tools.code_execution_tool",
        "hermes_cli.pty_bridge",
        "tools.approval",
    ],
    "skills": [
        "agent.skill_utils",
        "agent.skill_preprocessing",
        "agent.skill_commands",
        "tools.skill_usage",
    ],
    "memory": [
        "agent.memory_manager",
        "agent.memory_provider",
        "hermes_cli.memory_setup",
    ],
    "gateway": [
        "gateway.run",
        "gateway.status",
        "hermes_cli.gateway",
    ],
    "trajectory": [
        "agent.trajectory",
        "trajectory_compressor",
        "batch_runner",
    ],
    "setup_doctor": [
        "hermes_cli.setup",
        "hermes_cli.doctor",
    ],
}


def module_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


class OptionalAdapterBridge:
    """Read-only detection boundary for optional framework adapters."""

    def __init__(self, *, import_core: bool = False):
        self.import_core = import_core

    def available_capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {}
        for name, modules in HERMES_CAPABILITY_MODULES.items():
            available_modules = [module for module in modules if module_available(module)]
            capabilities[name] = {
                "available": bool(available_modules),
                "detected_modules": available_modules,
                "candidate_modules": modules,
                "bridge_mode": "read_only_detection",
                "adapter": "hermes",
            }
        return capabilities

    def detected_adapters(self) -> list[str]:
        capabilities = self.available_capabilities()
        return ["hermes"] if any(item["available"] for item in capabilities.values()) else []

    def manifest(self) -> dict[str, Any]:
        capabilities = self.available_capabilities()
        detected = ["hermes"] if any(item["available"] for item in capabilities.values()) else []
        manifest = agent_manifest()
        manifest["adapter_bridge"] = {
            "mode": "optional_read_only_detection",
            "imports_adapter_modules": self.import_core,
            "capabilities": capabilities,
            "policy": (
                "Praxile runs as a standalone agent harness. Framework adapters are optional, "
                "read-only by default, and must not own .praxile state."
            ),
            "supported_adapters": ["hermes"],
            "detected_adapters": detected,
        }
        return manifest

    def adapter_matrix(self) -> dict[str, dict[str, str]]:
        return self.manifest()["adapter_matrix"]
