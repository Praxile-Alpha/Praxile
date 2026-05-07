from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import __version__


AGENT_ID = "praxile.local-self-evolving-agent"

AGENT_BOUNDARY = {
    "agent": "Praxile is a standalone local self-evolving agent harness built around Environment -> Reward -> Experience.",
    "state": "Praxile owns .praxile/ state, project memory, project skills, trajectories, reward reports, proposals, approvals, rollback, architecture gates, and harness rules.",
    "adapter_rule": "Hermes, OpenClaw, and other agent frameworks are optional adapters, not required parents or product surfaces.",
    "brand_rule": "Do not present Praxile's self-evolution loop as an external framework feature.",
    "upgrade_rule": "Keep optional adapter touch points behind narrow detection/interop modules so Praxile can run without those frameworks.",
}

ADAPTER_MATRIX: dict[str, dict[str, str]] = {
    "provider": {
        "optional_adapter": "Hermes/OpenClaw/provider configs may inform endpoint setup when explicitly detected.",
        "praxile": "Owns task-aware model routing policy and routing proposals for local code-project evolution.",
        "boundary": "Praxile can run directly against OpenAI-compatible, local, or cloud endpoints without another agent framework.",
    },
    "runtime": {
        "optional_adapter": "Framework runtimes may be inspected later through explicit adapters.",
        "praxile": "Owns the local task analysis, context retrieval, action loop, trajectory logging, reward, and experience extraction runtime.",
        "boundary": "The Praxile runtime is invoked through `praxile run` and does not depend on Hermes/OpenClaw chat loops.",
    },
    "tools_terminal": {
        "optional_adapter": "External terminal/tool systems may become future adapters.",
        "praxile": "Owns conservative FileSystemEnv, GitEnv, ShellEnv, and TestEnv for auditable code-project tasks.",
        "boundary": "Tool execution remains behind Praxile safety policy and local environment adapters.",
    },
    "skills": {
        "optional_adapter": "External native skill stores are separate systems.",
        "praxile": "Owns project-local .praxile/skills/*/SKILL.md retrieval and skill proposals.",
        "boundary": ".praxile skills are loaded by Praxile itself and are not auto-installed into Hermes/OpenClaw.",
    },
    "memory": {
        "optional_adapter": "External global memory/profile systems are separate systems.",
        "praxile": "Owns project-local memory/project.md, decisions.md, failures.md, and repository-scoped user.md.",
        "boundary": "Project facts never write to external/global memory automatically; export requires an explicit future proposal.",
    },
    "gateway": {
        "optional_adapter": "Messaging gateways are optional frontends outside the MVP.",
        "praxile": "Owns CLI-first local project execution.",
        "boundary": "Gateway delivery is not required for the self-evolution loop.",
    },
    "trajectory": {
        "optional_adapter": "External trajectory/research tooling may consume exports.",
        "praxile": "Owns structured audit trajectories plus an external-compatible JSONL sidecar.",
        "boundary": "The sidecar is an export envelope; the Praxile JSON trajectory remains the source of truth.",
    },
    "setup_doctor": {
        "optional_adapter": "External setup/doctor commands remain separate.",
        "praxile": "Owns praxile init, praxile doctor, and praxile interop.",
        "boundary": "Praxile setup/doctor validate the standalone harness and optional adapter detection.",
    },
}


def agent_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": AGENT_ID,
        "name": "Praxile",
        "version": __version__,
        "kind": "standalone_self_evolving_agent_harness",
        "scope": "local_code_project_self_evolution",
        "entrypoints": ["praxile"],
        "state_root": ".praxile/",
        "boundary": deepcopy(AGENT_BOUNDARY),
        "adapter_matrix": deepcopy(ADAPTER_MATRIX),
        "owned_capabilities": [
            "agent_runtime",
            "environment_adapters",
            "model_provider_layer",
            "project_local_memory",
            "project_local_skills",
            "trajectory_audit_log",
            "reward_report",
            "experience_proposals",
            "architecture_gate",
            "harness_rules",
            "approval_and_rollback",
        ],
        "explicit_non_goals": [
            "requiring Hermes or OpenClaw to run",
            "automatic external global memory writes",
            "automatic external native skill installation",
            "external gateway behavior changes",
            "external runtime monkey-patching",
            "automatic model parameter training",
            "marketplace or multi-agent orchestration",
        ],
    }


def format_adapter_matrix(matrix: dict[str, dict[str, str]] | None = None) -> list[str]:
    rows = matrix or ADAPTER_MATRIX
    lines = ["Adapter Matrix"]
    for capability, values in rows.items():
        lines.append(f"- {capability}")
        lines.append(f"  Optional Adapter: {values['optional_adapter']}")
        lines.append(f"  Praxile: {values['praxile']}")
        lines.append(f"  Boundary: {values['boundary']}")
    return lines
