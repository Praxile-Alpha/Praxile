from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import Config
from .adapter_bridge import OptionalAdapterBridge
from .identity import format_adapter_matrix


PRAXILE_TRAJECTORY_SCHEMA = "praxile_trajectory_v1"
EXTERNAL_COMPAT_TRAJECTORY_FORMAT = "sharegpt_jsonl_sidecar_v1"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def interop_policy(config: Config) -> dict[str, Any]:
    bridge = OptionalAdapterBridge()
    manifest = bridge.manifest()
    guard = external_agent_conflict(config)
    return {
        "agent": {
            "id": manifest["id"],
            "name": manifest["name"],
            "kind": manifest["kind"],
            "scope": manifest["scope"],
            "state_root": manifest["state_root"],
            "boundary": manifest["boundary"],
            "owned_capabilities": manifest["owned_capabilities"],
            "explicit_non_goals": manifest["explicit_non_goals"],
            "adapter_matrix": manifest["adapter_matrix"],
            "adapter_bridge": manifest["adapter_bridge"],
        },
        "hermes_home": str(hermes_home()),
        "project_root": str(config.paths.root),
        "state_root": str(config.paths.state),
        "skills": {
            "external_framework_autoloads_praxile_skills": False,
            "praxile_loads_project_skills": True,
            "scope": "project",
            "source": ".praxile/skills/*/SKILL.md",
            "loaded_by": "praxile.AgentRuntime context retrieval",
            "not_loaded_by": "Hermes/OpenClaw CLI or gateway runtime unless a future explicit sync command is added",
            "priority": [
                "accepted frozen-boundary, architecture-gate, and harness-rule assets",
                "task-matching .praxile/skills/*/SKILL.md",
                "task-matching .praxile/evals/**/*.md",
                "project/decision/failure memory",
                "project-scoped user memory",
            ],
        },
        "memory": {
            "external_global_memory_write": "never automatic",
            "project_memory_write": ".praxile/memory/project.md",
            "decision_memory_write": ".praxile/memory/decisions.md",
            "failure_memory_write": ".praxile/memory/failures.md",
            "project_scoped_user_memory_write": ".praxile/memory/user.md",
            "rule": (
                "Project facts, schemas, commands, architecture notes, and failures stay project-local. "
                "Stable cross-project user preferences require an explicit future global-memory export proposal."
            ),
        },
        "trajectory": {
            "primary": ".praxile/experience/trajectories/YYYY-MM-DD-task_id.json",
            "primary_schema": PRAXILE_TRAJECTORY_SCHEMA,
            "external_native_reuse": "not the same in-memory schema as Hermes/OpenClaw trajectory tooling",
            "compat_sidecar": ".praxile/experience/trajectories/external_compat.jsonl",
            "compat_format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
            "research_pipeline_note": (
                "The sidecar preserves a ShareGPT-style conversation envelope for external compression/research import, "
                "while the structured Praxile JSON remains the audit source of truth."
            ),
        },
        "guardrails": guard,
    }


def external_agent_conflict(config: Config) -> dict[str, Any]:
    """Detect coarse write-conflict signals from optional neighboring agent frameworks."""

    signals: list[dict[str, str]] = []
    for raw_path in config.get("interop_guardrails", "lock_files", default=[]) or []:
        raw = Path(str(raw_path)).expanduser()
        lock_path = (raw if raw.is_absolute() else config.paths.root / raw).resolve(strict=False)
        try:
            rel_path = lock_path.relative_to(config.paths.root.resolve())
        except ValueError:
            continue
        if lock_path.exists():
            signals.append({"kind": "lock_file", "path": rel_path.as_posix()})
    for env_name in config.get("interop_guardrails", "environment_flags", default=[]) or []:
        if os.environ.get(str(env_name)):
            signals.append({"kind": "environment_flag", "name": str(env_name)})
    enabled = bool(config.get("interop_guardrails", "refuse_writes_when_external_agent_detected", default=True))
    return {
        "enabled": enabled,
        "blocked": enabled and bool(signals),
        "signals": signals,
        "policy": (
            "Praxile refuses normal project writes when configured external-agent lock signals are present. "
            "This prevents two agent runtimes from editing the same repository concurrently."
        ),
    }


def format_interop_policy(policy: dict[str, Any]) -> str:
    agent = policy["agent"]
    skill = policy["skills"]
    memory = policy["memory"]
    trajectory = policy["trajectory"]
    guardrails = policy.get("guardrails", {})
    lines = [
        "Praxile Interop Policy",
        "",
        "Agent Boundary",
        f"- Agent: {agent['name']} ({agent['id']})",
        f"- Kind: {agent['kind']}",
        f"- Scope: {agent['scope']}",
        f"- State root: {agent['state_root']}",
        f"- Agent rule: {agent['boundary']['agent']}",
        f"- State rule: {agent['boundary']['state']}",
        f"- Adapter rule: {agent['boundary']['adapter_rule']}",
        f"- Brand rule: {agent['boundary']['brand_rule']}",
        f"- Upgrade rule: {agent['boundary']['upgrade_rule']}",
        "",
        f"Project root: {policy['project_root']}",
        f"Hermes home (optional adapter): {policy['hermes_home']}",
        f"Praxile root: {policy['state_root']}",
        "",
        *format_adapter_matrix(agent["adapter_matrix"]),
        "",
        "Adapter Bridge Status",
        f"- Mode: {agent['adapter_bridge']['mode']}",
        f"- Supported adapters: {', '.join(agent['adapter_bridge']['supported_adapters']) or '(none)'}",
        f"- Detected adapters: {', '.join(agent['adapter_bridge']['detected_adapters']) or '(none)'}",
        f"- Imports adapter modules: {agent['adapter_bridge']['imports_adapter_modules']}",
        f"- Policy: {agent['adapter_bridge']['policy']}",
    ]
    for name, value in agent["adapter_bridge"]["capabilities"].items():
        status = "available" if value["available"] else "not detected"
        detected = ", ".join(value["detected_modules"]) if value["detected_modules"] else "(none)"
        lines.append(f"- {name}: {status} ({detected})")
    lines.extend(
        [
            "",
            "Skills",
            f"- External framework autoloads .praxile skills: {skill['external_framework_autoloads_praxile_skills']}",
            f"- Praxile loads .praxile skills: {skill['praxile_loads_project_skills']}",
            f"- Scope: {skill['scope']}",
            f"- Source: {skill['source']}",
            "- Load priority:",
        ]
    )
    lines.extend(f"  {idx}. {item}" for idx, item in enumerate(skill["priority"], 1))
    lines.extend(
        [
            "",
            "Memory",
            f"- External global memory write: {memory['external_global_memory_write']}",
            f"- Project memory: {memory['project_memory_write']}",
            f"- Decision memory: {memory['decision_memory_write']}",
            f"- Failure memory: {memory['failure_memory_write']}",
            f"- Project-scoped user memory: {memory['project_scoped_user_memory_write']}",
            f"- Rule: {memory['rule']}",
            "",
            "Trajectory",
            f"- Primary: {trajectory['primary']}",
            f"- Primary schema: {trajectory['primary_schema']}",
            f"- External native reuse: {trajectory['external_native_reuse']}",
            f"- Compatibility sidecar: {trajectory['compat_sidecar']}",
            f"- Compatibility format: {trajectory['compat_format']}",
            f"- Research note: {trajectory['research_pipeline_note']}",
            "",
            "Write Conflict Guardrails",
            f"- Enabled: {guardrails.get('enabled')}",
            f"- Blocking writes now: {guardrails.get('blocked')}",
            f"- Signals: {', '.join(_format_guard_signal(item) for item in guardrails.get('signals', [])) or '(none)'}",
            f"- Policy: {guardrails.get('policy')}",
        ]
    )
    return "\n".join(lines)


def _format_guard_signal(signal: dict[str, str]) -> str:
    if signal.get("kind") == "lock_file":
        return f"lock:{signal.get('path')}"
    if signal.get("kind") == "environment_flag":
        return f"env:{signal.get('name')}"
    return str(signal)
