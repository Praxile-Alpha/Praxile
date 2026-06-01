from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .adapters import GenericJSONLAdapter
from .audit import audit_json, format_audit_report
from .channels import ChannelSystem
from .command_context import load_command_context
from .consolidation import ConsolidationEngine
from .config import Config, ProjectPaths, find_project_root
from .environment import FileSystemEnv, GitEnv, ProjectEnv, ShellEnv, TestEnv
from .eval import EvalRunner, EvalSuite
from .evolution import EvolutionEngine
from .feedback import FeedbackSemanticClassifier, build_feedback, extract_feedback_intents
from .gateway import serve_gateway
from .inspector import inspect_project
from .interop import format_interop_policy, interop_policy
from .memory import MemorySystem
from .model import ModelRouter
from .project_map import build_project_map
from .reflect import ReflectScope, build_reflect_ci_check, format_reflect_ci_markdown, format_reflect_markdown, format_reflect_summary
from .reward import RewardEngine
from .security import SafetyPolicy
from .services import (
    AuditService,
    ContextJuiceService,
    GraphService,
    GovernanceLoopService,
    PolicyService,
    ProposalService,
    RepositoryContextService,
    RepositoryMemoryTreeService,
    ReflectService,
    RunService,
    WorkflowService,
    format_context_status,
)
from .snapshot import SnapshotManager
from .skills import SkillSystem
from .specs import check_spec_file, format_spec_check, format_spec_compliance, verify_spec_compliance
from .store import ExperienceStore
from .terminal import TerminalSession
from .tools import ToolRegistry
from .trajectory import TrajectoryLogger
from .utils import append_jsonl, indent_block, read_json, safe_input, shorten, stable_hash, unified_diff, utc_now, write_json
from .workspace import WorkspaceManager, workspace_diff_summary


def load(project_root: Path) -> tuple[Config, ExperienceStore]:
    context = load_command_context(project_root, initialize=False)
    return context.config, context.store

def run_setup_wizard(args: argparse.Namespace, config: Config) -> None:
    provider = getattr(args, "provider", None)
    if provider is None:
        print("Model provider setup")
        print("1. none")
        print("2. local Ollama")
        print("3. OpenAI-compatible endpoint")
        print("4. Anthropic")
        choice = safe_input("Choose provider [1]: ").strip() or "1"
        provider = {"1": "none", "2": "ollama", "3": "openai-compatible", "4": "anthropic"}.get(choice, choice)
    configure_model_provider(
        config,
        provider=str(provider),
        model=getattr(args, "model", None),
        base_url=getattr(args, "base_url", None),
        api_key_env=getattr(args, "api_key_env", None),
    )
    channel = getattr(args, "channel", None)
    if channel is None and sys.stdin.isatty():
        channel = safe_input("Bind Telegram/Discord channel now? [none/telegram/discord] (none): ").strip() or "none"
    if channel and channel != "none":
        configure_channel_binding(config, args, platform=channel)

def configure_model_provider(
    config: Config,
    *,
    provider: str,
    model: str | None,
    base_url: str | None,
    api_key_env: str | None,
) -> None:
    provider = provider.strip().lower()
    if provider == "none":
        clear_model_configuration(config)
        print("Model provider left unconfigured.")
        return
    if provider == "ollama":
        provider_id = "local_ollama"
        provider_type = "ollama"
        base_url = base_url or prompt_default("Ollama OpenAI-compatible base URL", "http://localhost:11434/v1")
        api_key_env = api_key_env or prompt_default("Ollama API key env var", "OLLAMA_API_KEY")
        model = model or prompt_default("Ollama model name", "qwen2.5-coder:7b")
        local_first = True
    elif provider == "openai-compatible":
        provider_id = "openai_compatible"
        provider_type = "openai_compatible"
        base_url = base_url or prompt_default("OpenAI-compatible base URL", "https://api.openai.com/v1")
        api_key_env = api_key_env or prompt_default("API key env var", "OPENAI_API_KEY")
        model = model or prompt_default("Model name", "gpt-4o-mini")
        local_first = False
    elif provider == "anthropic":
        provider_id = "anthropic"
        provider_type = "anthropic"
        base_url = base_url or prompt_default("Anthropic base URL", "https://api.anthropic.com/v1")
        api_key_env = api_key_env or prompt_default("Anthropic API key env var", "ANTHROPIC_API_KEY")
        model = model or prompt_default("Anthropic model name", "claude-3-5-sonnet-latest")
        local_first = False
    else:
        raise ValueError(f"Unsupported setup provider: {provider}")
    clear_model_configuration(config)
    config.data["model_providers"] = {
        provider_id: {
            "type": provider_type,
            "base_url": base_url,
            "api_key_env": api_key_env,
            "timeout_seconds": 30,
            "models": [
                {
                    "name": model,
                    "role": "configured",
                    "context_window": 0,
                    "supports_tools": False,
                }
            ],
        }
    }
    role = {"provider": provider_id, "model": model}
    config.data["model_roles"] = {
        "coding_agent": dict(role),
        "evidence_extraction": dict(role),
        "experience_reflection": dict(role),
        "deep_project_pattern_mining": {**role, "max_context_runs": 20},
        "reward_judge": {**role, "mode": "optional"},
        "proposal_composer": dict(role),
        "review_recommendation": dict(role),
        "cheap_reasoner": {**role, "mode": "optional"},
        "feedback_classifier": {**role, "mode": "optional"},
        "attribution_judge": {**role, "mode": "optional"},
        "counterexample_checker": {**role, "mode": "optional"},
        "pattern_mining": {**role, "mode": "optional"},
        "project_pattern_composer": {**role, "mode": "optional"},
        "embedding": {"provider": "local", "model": "local_hash"},
    }
    target = f"{provider_id}:{model}"
    config.data["routing"] = {
        "default_model": target,
        "planning_model": target,
        "coding_model": target,
        "evolution_model": target,
        "private_model": target,
        "cheap_model": target,
        "fallback_backoff_seconds": 0.25,
        "fallbacks": {},
        "strategy": config.get("routing", "strategy", default={}),
    }
    config.data.setdefault("cost_control", {})["local_first"] = local_first
    print(f"Configured model provider `{provider_id}` with model `{model}`.")

def clear_model_configuration(config: Config) -> None:
    config.data["model_providers"] = {}
    config.data["model_roles"] = {"embedding": {"provider": "local", "model": "local_hash"}}
    strategy = config.get("routing", "strategy", default={})
    config.data["routing"] = {
        "fallback_backoff_seconds": 0.25,
        "fallbacks": {},
        "strategy": strategy,
    }

def configure_channel_binding(config: Config, args: argparse.Namespace, *, platform: str) -> None:
    channel_id = getattr(args, "channel_id", None) or prompt_default(f"{platform} channel/chat ID", "")
    if not channel_id:
        raise ValueError("channel setup requires --channel-id or an entered channel/chat ID")
    token_env = getattr(args, "token_env", None) or prompt_default(
        f"{platform} bot token env var",
        "TELEGRAM_BOT_TOKEN" if platform == "telegram" else "DISCORD_BOT_TOKEN",
    )
    ChannelSystem(config).bind(
        platform,
        channel_id,
        guild_id=getattr(args, "guild_id", None),
        mode=getattr(args, "mode", "notify"),
        token_env=token_env,
        make_default=True,
    )

def prompt_default(label: str, default: str) -> str:
    if not sys.stdin.isatty():
        return default
    value = safe_input(f"{label} [{default}]: ").strip()
    return value or default

def has_configured_models(config: Config) -> bool:
    providers = config.get("model_providers", default={})
    roles = config.get("model_roles", default={})
    return bool(providers) and any(
        isinstance(role, dict) and role.get("provider") != "local" and role.get("model")
        for name, role in roles.items()
        if name != "embedding"
    )

def run_fast_demo(args: argparse.Namespace, demo_root: Path) -> int:
    print("[1/6] Creating fast demo project")
    calculator = demo_root / "calculator.py"
    test_file = demo_root / "test_calculator.py"
    calculator.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    test_file.write_text(
        "from calculator import add\n\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    config = Config.load(demo_root)
    config.data.setdefault("runtime", {})["default_test_commands"] = [
        'python -m pytest -q -m "not slow and not integration"'
    ]
    store = ExperienceStore(config.paths)
    store.initialize(config)
    config.write()
    safety = SafetyPolicy(config)
    fs = FileSystemEnv(config, safety)
    project = ProjectEnv(config, fs, GitEnv(config), TestEnv(config, ShellEnv(config, safety)))
    task = "Fast demo: record calculator pytest repair experience"
    logger = TrajectoryLogger(task, project.snapshot(refresh=True))
    retrieved = store.retrieve("calculator pytest repair", limit=4)
    logger.set_loaded_context(retrieved)
    store.record_asset_usage(logger.task_id, retrieved, used_in_prompt=True)
    logger.set_task_analysis(
        {
            "task_type": "test",
            "risk_level": "low",
            "architecture_gate_required": False,
            "ui_human_review_required": False,
            "privacy_sensitive": False,
            "high_risk": False,
            "confidence": 1.0,
            "reasons": ["Fast deterministic demo; no model or real test run."],
            "signals": {"demo_fast": True},
            "frozen_hits": [],
        }
    )
    logger.set_plan(
        [
            "Record a synthetic failing test observation.",
            "Read the buggy calculator implementation.",
            "Apply the smallest scoped fix without calling a model.",
            "Record a synthetic passing verification observation.",
            "Generate proposal-only project memory.",
            "Optionally accept one low-risk memory proposal and retrieve it.",
        ]
    )
    print("[2/6] Recording synthetic failing test")
    logger.add_action(
        action_type="run_test",
        input_data={"command": 'python -m pytest -q -m "not slow and not integration"', "mode": "fast"},
        observation={
            "status": "failure",
            "output": "FAST DEMO: simulated failing assertion: assert add(2, 3) == 5",
            "data": {
                "command": 'python -m pytest -q -m "not slow and not integration"',
                "returncode": 1,
                "simulated": True,
            },
            "risk_level": "low",
        },
        status="failure",
    )
    read_observation = fs.read_file("calculator.py").to_dict()
    logger.add_action(
        action_type="read_file",
        input_data={"path": "calculator.py"},
        observation=read_observation,
        status=read_observation.get("status", "unknown"),
    )
    print("[3/6] Applying scoped fix")
    edit_observation = fs.write_file(
        "calculator.py",
        "def add(left, right):\n    return left + right  # fixed by Praxile fast demo\n",
        task_id=logger.task_id,
        step=len(logger.data["actions"]) + 1,
    ).to_dict()
    logger.add_action(
        action_type="edit_file",
        input_data={"path": "calculator.py"},
        observation=edit_observation,
        status=edit_observation.get("status", "unknown"),
    )
    print("[4/6] Recording synthetic verification")
    after_results = [
        {
            "status": "success",
            "output": "FAST DEMO: simulated passing pytest verification.",
            "data": {
                "command": 'python -m pytest -q -m "not slow and not integration"',
                "returncode": 0,
                "simulated": True,
            },
            "risk_level": "low",
        }
    ]
    for result in after_results:
        logger.add_action(
            action_type="run_test",
            input_data={"command": result.get("data", {}).get("command"), "mode": "fast"},
            observation=result,
            status=result.get("status", "unknown"),
        )
    logger.set_diff_summary({"diff": "", "summary": "Fast demo synthetic diff; calculator.py changed from subtraction to addition."})
    print("[5/6] Generating reward and proposals")
    trajectory = logger.finish(status="completed", summary="Recorded a fast calculator repair experience trajectory.")
    report = RewardEngine(config).build_report(trajectory, after_results)
    report.setdefault("notes", []).append("Fast demo verification is simulated; use the default fast pytest command in real projects.")
    trajectory["reward_report"] = report
    proposals = EvolutionEngine(config).generate(trajectory)
    trajectory["experience_candidates"] = [
        {
            "proposal_id": proposal["proposal_id"],
            "type": proposal["type"],
            "title": proposal["title"],
            "risk_level": proposal["risk_level"],
            "priority": proposal.get("priority"),
            "confidence": proposal.get("confidence"),
            "confidence_level": proposal.get("confidence_level"),
            "evidence_summary": proposal.get("evidence_summary"),
            "target_files": proposal["target_files"],
        }
        for proposal in proposals
    ]
    trajectory["evolution_summary"] = {
        "used_assets": len(trajectory.get("loaded_assets") or []),
        "used_asset_counts": {},
        "produced_proposals": len(proposals),
        "proposal_counts": _count_by(proposals, "type"),
        "proposal_risk_counts": _count_by(proposals, "risk_level"),
        "proposal_confidence_counts": _count_by(proposals, "confidence_level"),
        "experience_generation": report.get("experience_generation", {}),
        "review_command": f"praxile review --source-run {logger.task_id}",
    }
    store.record_trajectory(trajectory)
    store.update_asset_usage_outcome(logger.task_id, "success")
    for proposal in proposals:
        store.write_proposal(proposal)

    accepted_id = None
    retrieved_after_accept: list[dict] = []
    if args.accept_first:
        memory = next((proposal for proposal in proposals if proposal.get("type") == "memory_update"), None)
        if memory:
            accepted = ProposalService(store).accept(memory["proposal_id"], confirm=True)
            accepted_id = accepted["proposal_id"]
            retrieved_after_accept = store.retrieve("calculator pytest repair", kinds=["memory"], limit=5)

    print("[6/6] Showing next-run retrieval evidence")
    print(f"Praxile fast demo project: {demo_root}")
    print(f"Task: {trajectory['task_id']}")
    print("Mode: fast demo (verification simulated)")
    print("Before pytest: failure")
    print("After pytest: success")
    print(f"Reward overall: {report.get('overall')}")
    print_run_evolution_summary(trajectory)
    if accepted_id:
        print(f"Accepted demo memory proposal: {accepted_id}")
        print(f"Retrieval after accept: {len(retrieved_after_accept)} memory match(es)")
    else:
        print("No proposal was auto-accepted. Run:")
        print(f"  praxile --project {demo_root} review --interactive")
    print("Next steps:")
    print(f"1. praxile --project {demo_root} explain latest")
    print(f"2. praxile --project {demo_root} review --interactive")
    print('3. Run `praxile run "your task" --test-command "python -m pytest -q -m \\"not slow and not integration\\""` in your own repo.')
    if getattr(args, "show_files", False):
        print_demo_files(demo_root)
    return 0

def print_demo_files(demo_root: Path) -> None:
    state = demo_root / ".praxile"
    print("Generated files:")
    patterns = [
        "experience/trajectories/*.json",
        "experience/evidence/*.json",
        "experience/episodes/*.json",
        "experience/proposals/pending/*.json",
    ]
    for pattern in patterns:
        for path in sorted(state.glob(pattern))[:5]:
            print(f"- {path.relative_to(demo_root)}")
    for path in [state / "constitution.md", state / "memory" / "project.md"]:
        if path.exists():
            print(f"- {path.relative_to(demo_root)}")

def run_in_isolated_workspace(
    args: argparse.Namespace,
    config: Config,
    store: ExperienceStore,
    route_overrides: dict[str, str],
    workspace_mode: str,
) -> int:
    if args.resume:
        raise ValueError("run --resume is not supported with isolated workspace mode yet")
    manager = WorkspaceManager(config)
    record = manager.create(mode=workspace_mode, label=args.task or "run")
    workspace_config = Config(copy.deepcopy(config.data), ProjectPaths(record.root))
    workspace_config.write()
    workspace_store = ExperienceStore(workspace_config.paths)
    workspace_store.initialize(workspace_config)
    trajectory: dict[str, Any] | None = None
    try:
        trajectory = RunService(workspace_config, workspace_store).run(
            args.task or "",
            test_commands=args.test_command or None,
            max_steps=args.max_steps,
            dry_run=args.dry_run,
            spec_files=args.spec or None,
            parallel_readonly_explore=getattr(args, "parallel_readonly_explore", None),
        )
        diff_summary = workspace_diff_summary(config.paths.root, record.root)
        runtime_diff = trajectory.get("diff_summary")
        trajectory["workspace_runtime_diff_summary"] = runtime_diff
        trajectory["diff_summary"] = diff_summary
        diff_artifact = manager.write_diff_artifact(record.workspace_id, str(diff_summary.get("diff") or ""))
        imported = import_workspace_experience(
            config=config,
            store=store,
            workspace_config=workspace_config,
            trajectory=trajectory,
            record=record,
            diff_artifact=diff_artifact,
        )
        status = str(trajectory.get("result", {}).get("status") or "unknown")
        record = manager.update(
            record.workspace_id,
            status=status,
            task_id=trajectory.get("task_id"),
            diff_artifact=str(diff_artifact.relative_to(config.paths.root)) if diff_artifact else None,
            imported_proposals=imported["proposals"],
        )
        keep = (
            bool(args.keep_workspace)
            if getattr(args, "keep_workspace", None) is not None
            else bool(config.get("workspace", "keep_after_run", default=True))
        )
        print_isolated_run_result(trajectory, route_overrides, record, imported, diff_artifact=diff_artifact, keep_workspace=keep)
        if not keep:
            manager.remove(record.workspace_id)
        return 0
    except Exception as exc:
        manager.update(record.workspace_id, status="failed", error=f"{exc.__class__.__name__}: {exc}")
        raise

def import_workspace_experience(
    *,
    config: Config,
    store: ExperienceStore,
    workspace_config: Config,
    trajectory: dict[str, Any],
    record,
    diff_artifact: Path | None,
) -> dict[str, int]:
    workspace_store = ExperienceStore(workspace_config.paths)
    workspace_store.initialize(workspace_config)
    isolation = {
        "workspace_id": record.workspace_id,
        "mode": record.mode,
        "workspace_root": str(record.root),
        "source_root": str(record.source_root),
        "diff_artifact": str(diff_artifact.relative_to(config.paths.root)) if diff_artifact else None,
        "source_changes_applied": False,
        "imported_at": utc_now(),
    }
    trajectory["workspace_isolation"] = isolation
    store.record_trajectory(trajectory)
    imported_proposals = 0
    for proposal in workspace_store.list_proposals(status=None, limit=10000):
        proposal["workspace_isolation"] = isolation
        source = proposal.get("source") if isinstance(proposal.get("source"), dict) else {}
        source["workspace_id"] = record.workspace_id
        proposal["source"] = source
        store.write_proposal(proposal)
        imported_proposals += 1
    return {"trajectory": 1, "proposals": imported_proposals}

def print_isolated_run_result(
    trajectory: dict[str, Any],
    route_overrides: dict[str, str],
    record,
    imported: dict[str, int],
    *,
    diff_artifact: Path | None,
    keep_workspace: bool,
) -> None:
    print(f"Task: {trajectory['task_id']}")
    print(f"Status: {trajectory['result']['status']}")
    print(f"Workspace mode: {record.mode}")
    print(f"Workspace: {record.root}")
    print("Source changes applied: no")
    if diff_artifact:
        print(f"Patch artifact: {diff_artifact.relative_to(record.source_root)}")
    print(f"Imported trajectory/proposals: {imported.get('trajectory', 0)}/{imported.get('proposals', 0)}")
    print(f"Workspace retained: {'yes' if keep_workspace else 'no'}")
    if route_overrides:
        print("Route overrides:")
        for key, value in route_overrides.items():
            print(f"- {key}: {value}")
    report = trajectory.get("reward_report", {})
    print(f"Reward overall: {report.get('overall')}")
    if report.get("notes"):
        print("Reward notes:")
        for note in report["notes"]:
            print(f"- {note}")
    candidates = trajectory.get("experience_candidates", [])
    if candidates:
        print("Pending proposals:")
        for proposal in candidates:
            print(
                f"- {proposal['proposal_id']} [{proposal['type']}] "
                f"risk={proposal.get('risk_level')} confidence={proposal.get('confidence_level', proposal.get('confidence'))} "
                f"{proposal['title']}"
            )
    print_run_evolution_summary(trajectory)
    print(f"Review with: praxile review --source-run {trajectory['task_id']}")
    print(f"Explain with: praxile explain {trajectory['task_id']}")
    print("Inspect workspace state with: praxile workspace list")

def apply_run_overrides(args: argparse.Namespace, config: Config) -> dict[str, str]:
    mapping = {
        "model_default": "default_model",
        "model_planning": "planning_model",
        "model_coding": "coding_model",
        "model_evolution": "evolution_model",
        "model_private": "private_model",
        "model_cheap": "cheap_model",
    }
    role_mapping = {
        "model_coding": "coding_agent",
        "model_evolution": "experience_reflection",
        "model_cheap": "review_recommendation",
    }
    overrides: dict[str, str] = {}
    routing = config.data.setdefault("routing", {})
    roles = config.data.setdefault("model_roles", {})
    for attr, route_key in mapping.items():
        value = getattr(args, attr, None)
        if not value:
            continue
        target = normalize_model_route(str(value), current=str(config.get("routing", route_key, default="") or ""))
        routing[route_key] = target
        role_name = role_mapping.get(attr)
        if role_name:
            provider, model = target.split(":", 1)
            role = roles.setdefault(role_name, {})
            role["provider"] = provider
            role["model"] = model
        overrides[route_key] = target
    return overrides

def normalize_model_route(value: str, *, current: str = "") -> str:
    value = value.strip()
    if ":" in value:
        return value
    provider = "openai_compatible"
    if ":" in current:
        provider = current.split(":", 1)[0]
    return f"{provider}:{value}"

def emit_audit_report(report: dict[str, Any], args: argparse.Namespace) -> int:
    if getattr(args, "output", None):
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(audit_json(report) + "\n", encoding="utf-8")
        print(f"Wrote audit report: {output_path}", file=sys.stderr if getattr(args, "json", False) else sys.stdout)
    if getattr(args, "json", False):
        print(audit_json(report))
    else:
        print(format_audit_report(report))
    if report.get("audit_type") == "check":
        check = report.get("check") if isinstance(report.get("check"), dict) else {}
        return int(check.get("exit_code", 1))
    return 0 if report.get("found") else 1

def format_context_juice_status(payload: dict[str, Any]) -> str:
    lines = ["ContextJuice:"]
    profiles = payload.get("profiles") or {}
    lines.append(f"- profiles: {len(profiles)}")
    for role, profile in sorted(profiles.items()):
        preserve = ", ".join(profile.get("preserve") or [])
        lines.append(f"  - {role}: max_chars={profile.get('max_chars')} preserve={preserve}")
    latest = payload.get("latest")
    if latest:
        lines.append(
            f"- latest: {latest.get('compression_id')} role={latest.get('role')} "
            f"ratio={latest.get('ratio')} source={latest.get('source_type')}"
        )
    else:
        lines.append("- latest: none")
    return "\n".join(lines)

def format_context_compression(payload: dict[str, Any]) -> str:
    lines = [
        "Context compression:",
        f"- id: {payload.get('compression_id')}",
        f"- role: {payload.get('role')}",
        f"- source: {payload.get('source_type')} {payload.get('source_id') or ''}".rstrip(),
        f"- chars: {payload.get('raw_chars')} -> {payload.get('compressed_chars')} ratio={payload.get('ratio')}",
        f"- estimated savings: {payload.get('estimated_savings')}",
    ]
    if payload.get("markdown_path"):
        lines.append(f"- markdown: {payload.get('markdown_path')}")
    evidence = payload.get("preserved_evidence") or {}
    lines.append(f"- preserved commands: {len(evidence.get('commands') or [])}")
    lines.append(f"- preserved paths: {len(evidence.get('file_paths') or [])}")
    lines.append(f"- preserved signatures: {len(evidence.get('error_signatures') or [])}")
    return "\n".join(lines)

def format_memory_tree(payload: dict[str, Any]) -> str:
    lines = [
        "Repository Memory Tree:",
        f"- id: {payload.get('tree_id')}",
        f"- modules: {payload.get('module_count')}",
        f"- active assets: {payload.get('asset_count')}",
        f"- recent runs: {payload.get('run_count')}",
    ]
    if payload.get("markdown_path"):
        lines.append(f"- markdown: {payload.get('markdown_path')}")
    else:
        lines.append("")
        lines.append(shorten(str(payload.get("markdown") or ""), 4000))
    return "\n".join(lines)

def format_policy_layers(payload: dict[str, Any]) -> str:
    lines = [f"Policy layers: {payload.get('policy_root')}"]
    for layer in payload.get("layers") or []:
        source = layer.get("source")
        exists = "present" if layer.get("exists") else "missing"
        keys = ", ".join(layer.get("keys") or [])
        lines.append(f"- {layer.get('name')}: {source}/{exists} keys={keys or '(none)'}")
    return "\n".join(lines)

def format_policy_check(payload: dict[str, Any]) -> str:
    lines = [f"Policy check: {'ok' if payload.get('ok') else 'failed'}"]
    for error in payload.get("errors") or []:
        lines.append(f"- ERROR {error.get('layer')}: {error.get('code')} {error.get('message')}")
    for warning in payload.get("warnings") or []:
        lines.append(f"- WARN {warning.get('layer')}: {warning.get('code')} {warning.get('message')}")
    if not payload.get("errors") and not payload.get("warnings"):
        lines.append("- no issues")
    return "\n".join(lines)

def format_policy_explain(payload: dict[str, Any]) -> str:
    lines = [f"Policy explain: {payload.get('topic')}", f"- {payload.get('why')}"]
    for match in payload.get("matched_layers") or []:
        lines.append(f"- {match.get('layer')} ({match.get('source')}): {json.dumps(match.get('value'), ensure_ascii=False)}")
    return "\n".join(lines)

def format_governance_report(payload: dict[str, Any]) -> str:
    lines = [
        "Governance loop:",
        f"- report: {payload.get('report_id')}",
        f"- path: {payload.get('path') or '(not written)'}",
        "- safety: no code edits, no auto-accept, no durable asset rewrite",
    ]
    for event in payload.get("events") or []:
        lines.append(f"- {event.get('step')}: {event.get('status')} {event.get('message')}")
    audit = payload.get("audit") or {}
    if audit:
        lines.append(f"- audit: ok={audit.get('ok')} failures={audit.get('failure_count')} warnings={audit.get('warning_count')}")
    return "\n".join(lines)

def format_workflow_list(payload: dict[str, Any]) -> str:
    lines = [f"Workflow templates: {payload.get('workflow_root')}"]
    for workflow in payload.get("workflows") or []:
        lines.append(
            f"- {workflow.get('name')} [{workflow.get('source')}] "
            f"spec={workflow.get('requires_spec')} tests={len(workflow.get('required_tests') or [])}"
        )
    return "\n".join(lines)

def format_workflow_show(payload: dict[str, Any]) -> str:
    workflow = payload.get("workflow") or {}
    lines = [
        f"Workflow: {payload.get('name')}",
        f"- source: {payload.get('source')}",
        f"- requires_spec: {workflow.get('requires_spec')}",
        f"- allowed_tools: {', '.join(workflow.get('allowed_tools') or [])}",
        f"- required_tests: {', '.join(workflow.get('required_tests') or [])}",
        f"- proposal_gate: {workflow.get('proposal_gate')}",
        f"- silent_failure_rules: {', '.join(workflow.get('silent_failure_rules') or [])}",
        f"- audit_outputs: {', '.join(workflow.get('audit_outputs') or [])}",
        f"- review_strategy: {workflow.get('review_strategy')}",
    ]
    return "\n".join(lines)

def resolve_feedback_target(store: ExperienceStore, args: argparse.Namespace) -> tuple[str, str]:
    target = str(args.target or "").strip()
    if target == "latest":
        trajectory = store.latest_trajectory()
        if not trajectory:
            raise ValueError("No latest run found for feedback.")
        return "run", str(trajectory["task_id"])
    if target == "asset":
        if not args.asset_path:
            raise ValueError("feedback asset requires <asset_path>")
        return "asset", normalize_asset_path(args.asset_path)
    if target == "pattern":
        if not args.asset_path:
            raise ValueError("feedback pattern requires <pattern_id> or <pattern_path>")
        return "pattern", normalize_asset_path(args.asset_path) if "/" in args.asset_path else str(args.asset_path)
    if target.startswith("asset:"):
        return "asset", normalize_asset_path(target.split(":", 1)[1])
    if target.startswith("pattern:"):
        value = target.split(":", 1)[1]
        return "pattern", normalize_asset_path(value) if "/" in value else value
    if target.startswith("proposal:"):
        proposal_id = target.split(":", 1)[1]
        if proposal_id.isdigit():
            proposals = store.list_proposals(status="pending")
            proposals.sort(key=proposal_sort_key)
            index = int(proposal_id) - 1
            if 0 <= index < len(proposals):
                return "proposal", str(proposals[index]["proposal_id"])
            raise ValueError(f"No pending proposal at position {proposal_id}")
        proposal = store.find_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"No proposal found: {proposal_id}")
        return "proposal", str(proposal["proposal_id"])
    if target in {"latest-proposal", "latest-prop"}:
        proposals = store.list_proposals(status="pending")
        if not proposals:
            raise ValueError("No pending proposal found for feedback.")
        proposals.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)
        return "proposal", str(proposals[0]["proposal_id"])
    if target.startswith("prop_"):
        proposal = store.find_proposal(target)
        if proposal:
            return "proposal", str(proposal["proposal_id"])
    proposal = store.find_proposal(target)
    if proposal:
        return "proposal", str(proposal["proposal_id"])
    trajectory = store.get_trajectory(target)
    if trajectory:
        return "run", str(trajectory["task_id"])
    raise ValueError(f"Could not resolve feedback target: {target}")

def resolve_feedback_intent_target(store: ExperienceStore, intent: dict[str, Any]) -> tuple[str, str]:
    target_type = str(intent.get("target_type") or "run")
    hint = str(intent.get("target_hint") or "latest")
    if target_type == "run":
        trajectory = store.latest_trajectory()
        if not trajectory:
            raise ValueError("No latest run found for feedback.")
        return "run", str(trajectory["task_id"])
    if target_type == "proposal":
        proposals = store.list_proposals(status="pending")
        proposals.sort(key=proposal_sort_key)
        if hint.startswith("nth:"):
            index = int(hint.split(":", 1)[1]) - 1
            if 0 <= index < len(proposals):
                return "proposal", str(proposals[index]["proposal_id"])
            raise ValueError(f"No pending proposal at position {index + 1}")
        if proposals:
            proposals.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""), reverse=True)
            return "proposal", str(proposals[0]["proposal_id"])
        raise ValueError("No pending proposal found for feedback.")
    if target_type == "asset":
        if hint == "recent_loaded_asset":
            trajectory = store.latest_trajectory()
            for item in reversed(trajectory.get("loaded_assets") or [] if trajectory else []):
                path = item.get("path") or item.get("asset_id")
                if path:
                    return "asset", normalize_asset_path(str(path))
            raise ValueError("Could not resolve recent loaded asset; provide `asset:<path>`.")
        return "asset", normalize_asset_path(hint)
    if target_type == "pattern":
        return "pattern", hint
    raise ValueError(f"Unsupported feedback target type: {target_type}")

def feedback_semantic_context(store: ExperienceStore) -> dict[str, Any]:
    latest = store.latest_trajectory()
    proposals = store.list_proposals(status="pending")
    proposals.sort(key=proposal_sort_key)
    return {
        "latest_run_id": latest.get("task_id") if latest else None,
        "latest_proposals": [
            {
                "index": index + 1,
                "proposal_id": proposal.get("proposal_id"),
                "type": proposal.get("type"),
                "title": proposal.get("title"),
                "risk_level": proposal.get("risk_level"),
                "confidence": proposal.get("confidence"),
            }
            for index, proposal in enumerate(proposals[:10])
        ],
        "latest_loaded_assets": [
            {
                "path": item.get("path") or item.get("asset_id"),
                "title": item.get("title"),
                "kind": item.get("kind") or item.get("type"),
                "why_loaded": item.get("why_loaded"),
            }
            for item in (latest.get("loaded_assets") or [])[:10]
        ]
        if latest
        else [],
    }

def resolve_feedback_sentiment(args: argparse.Namespace) -> tuple[str | None, str]:
    for attr, sentiment, fallback in [
        ("positive", "positive", "positive feedback"),
        ("negative", "negative", "negative feedback"),
        ("helpful", "positive", "helpful"),
        ("harmful", "negative", "harmful"),
    ]:
        value = getattr(args, attr, None)
        if value is not None:
            text = str(value or args.text or fallback)
            return sentiment, text
    if args.text:
        return None, str(args.text)
    raise ValueError("feedback requires --positive, --negative, --helpful, --harmful, or --text")

def update_run_reward_after_feedback(config: Config, store: ExperienceStore, task_id: str) -> None:
    trajectory = store.get_trajectory(task_id)
    if not trajectory:
        return
    trajectory["user_feedback_reward"] = store.feedback_reward_for("run", task_id)
    test_results = trajectory.get("reward_report", {}).get("test_results", [])
    trajectory["reward_report"] = RewardEngine(config).build_report(trajectory, test_results)
    store.update_trajectory(trajectory)

def write_asset_feedback_proposal(store: ExperienceStore, feedback: dict) -> dict:
    target = normalize_asset_path(str(feedback.get("target_id") or ""))
    change_path = target.removeprefix(".praxile/")
    proposal_id = f"prop_{stable_hash(feedback['feedback_id'] + target, length=12)}"
    proposal = {
        "proposal_id": proposal_id,
        "source_task_id": None,
        "source_trajectory_id": None,
        "type": "asset_deprecate",
        "title": f"Review harmful feedback for `{target}`",
        "reason": "Negative user feedback on a durable asset must be governed; Praxile should not silently rewrite memory, rules, or skills.",
        "target_files": [change_path],
        "diff": "",
        "risk_level": "medium",
        "priority": "p1",
        "source": {"type": "user_feedback", "feedback_id": feedback.get("feedback_id")},
        "evidence": [
            f"User feedback: {feedback.get('raw_text')}",
            f"Sentiment: {feedback.get('sentiment')} strength={feedback.get('strength')}",
        ],
        "evidence_summary": "User reported this accepted experience asset as harmful or misleading.",
        "affected_files": [target],
        "trigger_reason": "Negative asset feedback requires explicit review before changing durable project experience.",
        "confidence": 0.72,
        "confidence_level": "medium",
        "future_applicability": "Governed cleanup for misleading or harmful project-local experience assets.",
        "applicability_scope": "Only this asset unless the reviewer confirms similar assets share the same flaw.",
        "anti_scope": "Do not delete or rewrite durable experience silently from feedback alone.",
        "requires_user_approval": True,
        "requires_manual_review": True,
        "status": "pending",
        "generated_by": "user_feedback_governance",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "changes": [
            {
                "path": change_path,
                "operation": "metadata_update",
                "metadata": {
                    "status": "deprecated",
                    "deprecated_reason": f"User harmful feedback: {feedback.get('raw_text')}",
                    "deprecated_at": utc_now(),
                    "source_feedback_id": feedback.get("feedback_id"),
                },
            }
        ],
        "feedback_influence": [
            {
                "type": "negative_asset_feedback",
                "feedback_id": feedback.get("feedback_id"),
                "effect": "proposal_only_deprecation_review",
            }
        ],
    }
    return store.write_proposal(proposal) and proposal

def _cli_or_config_int(args: argparse.Namespace, name: str, fallback: Any) -> int | None:
    value = getattr(args, name, None)
    if value is None:
        value = fallback
    if value is None:
        return None
    return int(value)

def _write_reflect_ci_artifacts(config: Config, report: dict[str, Any], args: argparse.Namespace) -> None:
    output_dir_value = args.ci_output_dir or config.get(
        "reflect",
        "ci",
        "artifact_dir",
        default=".praxile/experience/reflect/ci",
    )
    output_dir = _project_output_path(config.paths.root, output_dir_value)
    output_dir.mkdir(parents=True, exist_ok=True)
    reflect_id = str(report.get("reflect_id") or "reflect")
    json_path = output_dir / f"{reflect_id}.json"
    markdown_path = output_dir / f"{reflect_id}.md"
    latest_json_path = output_dir / "latest.json"
    latest_markdown_path = output_dir / "latest.md"
    report["ci_artifacts"] = {
        "json": str(json_path.relative_to(config.paths.root)) if _is_relative_to(json_path, config.paths.root) else str(json_path),
        "markdown": str(markdown_path.relative_to(config.paths.root)) if _is_relative_to(markdown_path, config.paths.root) else str(markdown_path),
        "latest_json": str(latest_json_path.relative_to(config.paths.root)) if _is_relative_to(latest_json_path, config.paths.root) else str(latest_json_path),
        "latest_markdown": str(latest_markdown_path.relative_to(config.paths.root)) if _is_relative_to(latest_markdown_path, config.paths.root) else str(latest_markdown_path),
    }
    markdown = format_reflect_ci_markdown(report)
    write_json(json_path, report)
    write_json(latest_json_path, report)
    markdown_path.write_text(markdown, encoding="utf-8")
    latest_markdown_path.write_text(markdown, encoding="utf-8")
    if _should_write_github_step_summary(config, args):
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).expanduser().open("a", encoding="utf-8") as handle:
                handle.write("\n")
                handle.write(markdown)

def _should_write_github_step_summary(config: Config, args: argparse.Namespace) -> bool:
    if getattr(args, "no_github_step_summary", False):
        return False
    if getattr(args, "github_step_summary", False):
        return True
    return bool(config.get("reflect", "ci", "write_github_step_summary", default=True))

def _project_output_path(project_root: Path, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path

def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False

def normalize_asset_path(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"

def print_index_status(status: dict[str, object]) -> None:
    print("Experience index:")
    print(f"- assets expected: {status.get('assets_expected')}")
    print(f"- assets indexed: {status.get('assets_indexed')}")
    print(f"- vectors indexed: {status.get('vectors_indexed')}")
    print(f"- fts available: {status.get('fts_available')}")
    print(f"- pending events: {status.get('pending_events')}")
    print(f"- deep scan: {status.get('deep_scan')}")
    print(f"- needs rebuild: {status.get('needs_rebuild')}")
    missing = status.get("missing") or []
    stale = status.get("stale") or []
    vector_missing = status.get("vectors_missing") or []
    vector_stale = status.get("vectors_stale") or []
    if missing:
        print("Missing assets:")
        for path in list(missing)[:20]:
            print(f"- {path}")
    if stale:
        print("Stale assets:")
        for path in list(stale)[:20]:
            print(f"- {path}")
    if vector_missing:
        print("Missing vector rows:")
        for path in list(vector_missing)[:20]:
            print(f"- {path}")
    if vector_stale:
        print("Stale vector rows:")
        for path in list(vector_stale)[:20]:
            print(f"- {path}")

def print_graph_status(status: dict[str, object]) -> None:
    print("Experience graph:")
    print(f"- nodes: {status.get('nodes')}")
    print(f"- edges: {status.get('edges')}")
    node_counts = status.get("node_counts") or {}
    if node_counts:
        print("Node counts:")
        for key, value in sorted(dict(node_counts).items()):
            print(f"- {key}: {value}")
    print_relation_counts(status.get("relation_counts") or {})
    rebuild = status.get("last_rebuild")
    if isinstance(rebuild, dict):
        print("Last rebuild:")
        print(f"- nodes: {rebuild.get('nodes')}")
        print(f"- edges: {rebuild.get('edges')}")

def print_relation_counts(counts: dict[str, object]) -> None:
    if not counts:
        print("Relation counts: (none)")
        return
    print("Relation counts:")
    for key, value in sorted(counts.items()):
        print(f"- {key}: {value}")

def print_graph_report(report: dict[str, object], *, title: str) -> None:
    print(title)
    if not report.get("found"):
        print(f"- not found: {report.get('ref')}")
        print("Tip: run `praxile graph rebuild`, or pass an asset path, proposal id, task id, or spec path.")
        return
    start = report.get("start_node") if isinstance(report.get("start_node"), dict) else {}
    print(
        "Start: "
        f"{start.get('node_id')} "
        f"type={start.get('node_type')} "
        f"ref={start.get('ref_path') or '(none)'}"
    )
    print_relation_counts(report.get("relation_counts") or {})
    nodes = report.get("nodes") or []
    print(f"Nodes ({len(nodes)}):")
    for node in list(nodes)[:40]:
        if not isinstance(node, dict):
            continue
        print(
            f"- {node.get('node_id')} "
            f"[{node.get('node_type')}] "
            f"{shorten(str(node.get('title') or ''), 100)}"
        )
    edges = report.get("edges") or []
    print(f"Edges ({len(edges)}):")
    for edge in list(edges)[:80]:
        if not isinstance(edge, dict):
            continue
        evidence = edge.get("evidence") if isinstance(edge.get("evidence"), dict) else {}
        if evidence:
            evidence_text = shorten(json.dumps(evidence, ensure_ascii=False, sort_keys=True), 140)
        elif edge.get("evidence"):
            evidence_text = shorten(str(edge.get("evidence")), 140)
        else:
            evidence_text = "{}"
        print(
            f"- {edge.get('source_node_id')} --{edge.get('relation_type')} "
            f"({edge.get('confidence')})--> {edge.get('target_node_id')} "
            f"evidence={evidence_text}"
        )

def filter_and_sort_proposals(proposals: list[dict], args: argparse.Namespace) -> list[dict]:
    result = list(proposals)
    proposal_type = getattr(args, "proposal_type", None)
    risk = getattr(args, "risk", None)
    confidence = getattr(args, "confidence", None)
    source_run = getattr(args, "source_run", None)
    older_than = getattr(args, "older_than", None)
    if proposal_type:
        result = [proposal for proposal in result if proposal.get("type") == proposal_type]
    if risk:
        result = [proposal for proposal in result if proposal.get("risk_level", "low") == risk]
    if getattr(args, "high_risk", False):
        result = [proposal for proposal in result if proposal.get("risk_level", "low") == "high"]
    if confidence:
        result = [proposal for proposal in result if proposal_confidence_level(proposal) == confidence]
    if source_run:
        result = [
            proposal
            for proposal in result
            if str(proposal.get("source_task_id") or proposal.get("source", {}).get("task_id") or "").startswith(source_run)
        ]
    if older_than:
        result = [proposal for proposal in result if proposal_older_than(proposal, older_than)]
    result.sort(key=proposal_sort_key)
    return result

def filter_by_recommended_action(store: ExperienceStore, proposals: list[dict], recommended: str | None) -> list[dict]:
    if not recommended:
        return proposals
    return [
        proposal
        for proposal in proposals
        if proposal_review_guidance(store, proposal)["action"] == recommended
    ]

def proposal_plain_language(proposal: dict) -> str:
    mapping = {
        "memory_update": "Praxile wants to remember a project-local lesson from this run for similar future tasks.",
        "skill_create": "Praxile wants to turn this run into a reusable step-by-step project skill.",
        "eval_case": "Praxile wants to add a checklist or regression case for future verification.",
        "failure_pattern": "Praxile wants to record a failure signature so it can avoid repeating the same mistake.",
        "architecture_gate": "Praxile wants future architecture-sensitive changes to pause for human review before editing.",
        "frozen_boundary": "Praxile wants to mark a high-risk project boundary that should not be changed casually.",
        "harness_rule": "Praxile wants to change its future execution behavior for matching tasks.",
        "routing": "Praxile wants to remember model-routing evidence for future route choices.",
        "experience_consolidation": "Praxile wants a human to review accumulated experience hygiene findings.",
        "asset_deprecate": "Praxile wants to mark an old or low-value experience asset inactive so it stops loading by default.",
        "asset_merge": "Praxile wants to keep one canonical experience asset and supersede overlapping duplicates.",
        "asset_supersede": "Praxile wants to replace one experience asset with a better one.",
        "asset_rewrite": "Praxile wants to rewrite an experience asset after review.",
        "asset_archive": "Praxile wants to archive an obsolete experience asset so it stays auditable but no longer loads.",
        "asset_reactivate": "Praxile wants to restore a retired experience asset to active retrieval.",
    }
    return mapping.get(str(proposal.get("type")), "Praxile wants to update project-local experience after user approval.")

def proposal_review_guidance(store: ExperienceStore | None, proposal: dict) -> dict[str, str]:
    risk = str(proposal.get("risk_level", "low"))
    confidence = proposal_confidence_level(proposal)
    proposal_type = str(proposal.get("type") or "")
    duplicate = proposal_duplicate_warning(store, proposal) if store else ""
    duplicate_confidence = _duplicate_warning_confidence(duplicate)
    feedback = proposal_feedback_context(store, proposal) if store else {}
    feedback_note = str(feedback.get("note") or "")
    judge = proposal.get("llm_judge") if isinstance(proposal.get("llm_judge"), dict) else {}
    try:
        judge_risk = float(judge.get("overgeneralization_risk") or 0.0) if judge else 0.0
    except (TypeError, ValueError):
        judge_risk = 0.0
    counterexamples = proposal.get("counterexamples") if isinstance(proposal.get("counterexamples"), list) else []
    override = proposal.get("recommended_action_override")
    direct_feedback = proposal.get("user_feedback") if isinstance(proposal.get("user_feedback"), dict) else {}
    if override in {"accept", "inspect", "reject_or_edit", "inspect_duplicate"}:
        action = str(override)
        why = "Direct user feedback changed this proposal's recommendation; inspect or edit before accepting."
    elif counterexamples:
        action = "inspect"
        why = f"{len(counterexamples)} counterexample(s) constrain this proposal; inspect scope before accepting."
    elif judge and judge_risk >= 0.65:
        action = "reject_or_edit"
        why = "LLM judge flagged high overgeneralization risk; edit scope/evidence before accepting."
    elif int(direct_feedback.get("negative_count") or 0) > int(direct_feedback.get("positive_count") or 0):
        action = "reject_or_edit"
        why = "Direct negative user feedback says this proposal is low value, too generic, or needs editing."
    elif risk == "high" or proposal_type in {"architecture_gate", "frozen_boundary", "routing"}:
        action = "inspect"
        why = "This proposal affects future architecture, routing, or high-risk behavior."
        if duplicate:
            why += " A similar active asset also exists, so compare scope before accepting."
    elif duplicate and duplicate_confidence in {"high", "medium"}:
        action = "inspect_duplicate"
        why = "A similar or target experience asset already exists; accepting blindly may pollute retrieval."
    elif confidence == "low":
        if int(feedback.get("positive_outcomes") or 0) > int(feedback.get("negative_outcomes") or 0):
            action = "inspect"
            why = "Evidence is low-confidence, but similar accepted assets have positive outcomes; inspect before deciding."
        else:
            action = "reject_or_edit"
            why = "Evidence is low-confidence or too generic; edit before accepting if the idea is useful."
    elif risk == "low" and confidence == "high" and proposal_type in {
        "memory_update",
        "eval_case",
        "failure_pattern",
        "asset_deprecate",
        "asset_merge",
        "asset_supersede",
        "asset_archive",
    }:
        action = "accept"
        why = "Low-risk project-local proposal with enough confidence and auditable rollback."
    else:
        action = "inspect"
        why = "Review scope, evidence, and target files before accepting."
    if duplicate and duplicate_confidence == "possible" and action == "accept":
        why += " A possible overlap exists, but prior feedback or weak similarity makes it informational."
    if feedback_note:
        why += f" {feedback_note}"
    if judge.get("reasons"):
        why += " Judge: " + "; ".join(str(item) for item in judge.get("reasons", [])[:2])
    return {
        "action": action,
        "why": why,
        "will_affect": proposal_will_affect(proposal),
        "rollback": f"praxile rollback {proposal.get('proposal_id', '<PROPOSAL_ID>')}",
        "duplicate_warning": duplicate,
        "feedback_note": feedback_note,
        "judge_note": "; ".join(str(item) for item in judge.get("reasons", [])[:3]) if judge else "",
    }

def proposal_will_affect(proposal: dict) -> str:
    targets = proposal.get("target_files") or []
    proposal_type = str(proposal.get("type") or "")
    if proposal_type.startswith("asset_"):
        return "Experience retrieval for the targeted asset lifecycle metadata."
    if proposal_type in {"architecture_gate", "frozen_boundary", "harness_rule", "routing"}:
        return "Future runtime behavior for matching high-risk or governed tasks."
    if proposal_type == "skill_create":
        return "Future similar tasks that load active project skills."
    if proposal_type == "memory_update":
        return "Future retrieval for tasks matching the recorded files, commands, or failure signature."
    if proposal_type == "failure_pattern":
        return "Future repair tasks with a similar failure signature or command."
    if proposal_type == "eval_case":
        return "Future review and verification checklists."
    return f"Target files: {', '.join(targets[:3]) if targets else '(none)'}"

def proposal_governance_preview(proposal: dict) -> list[str]:
    if not str(proposal.get("type") or "").startswith("asset_"):
        return []
    lines = ["Governance preview:"]
    metadata_changes: list[str] = []
    content_changes: list[str] = []
    for change in proposal.get("changes") or []:
        if not isinstance(change, dict):
            continue
        path = str(change.get("path") or "")
        operation = str(change.get("operation", "write"))
        if operation == "metadata_update":
            metadata = change.get("metadata") if isinstance(change.get("metadata"), dict) else {}
            changed_keys = ", ".join(sorted(str(key) for key in metadata.keys())) or "(none)"
            metadata_changes.append(f"- metadata diff `{path}`: {changed_keys}")
        elif operation in {"write", "append"}:
            content = str(change.get("content") or "")
            line_count = len(content.splitlines())
            content_changes.append(f"- content diff `{path}`: {operation} {line_count} line(s)")
    if metadata_changes:
        lines.extend(metadata_changes)
    if content_changes:
        lines.extend(content_changes)
    lines.append(f"- retrieval impact: {proposal_will_affect(proposal)}")
    lines.append(f"- rollback path: praxile rollback {proposal.get('proposal_id', '<PROPOSAL_ID>')}")
    return lines if len(lines) > 3 else []

def proposal_feedback_context(store: ExperienceStore | None, proposal: dict) -> dict[str, object]:
    if not store:
        return {}
    query = _proposal_feedback_query(proposal)
    kinds = _proposal_retrieval_kinds(proposal)
    positive = 0
    negative = 0
    accepted_paths: list[str] = []
    if query:
        try:
            for match in store.retrieve(query, kinds=kinds, limit=5):
                accepted_paths.append(str(match.get("path")))
                positive += int(match.get("positive_outcome_count") or 0)
                negative += int(match.get("negative_outcome_count") or 0)
        except Exception:
            pass
    rejected_count = _matching_rejected_feedback_count(store, proposal)
    notes: list[str] = []
    if positive or negative:
        notes.append(f"Similar accepted assets have outcomes positive={positive} negative={negative}.")
    if rejected_count:
        notes.append(f"Similar rejected proposals in recent history: {rejected_count}.")
    return {
        "positive_outcomes": positive,
        "negative_outcomes": negative,
        "rejected_count": rejected_count,
        "accepted_paths": accepted_paths,
        "note": "Feedback: " + " ".join(notes) if notes else "",
    }

def _proposal_feedback_query(proposal: dict) -> str:
    signal = _proposal_similarity_signal(proposal)
    parts = sorted(signal["failure_signatures"])[:2] + sorted(signal["commands"])[:2] + sorted(signal["paths"])[:3]
    if not parts:
        parts = sorted(signal["terms"])[:8]
    return " ".join(parts)

def _matching_rejected_feedback_count(store: ExperienceStore, proposal: dict) -> int:
    proposal_terms = _proposal_similarity_signal(proposal)["terms"]
    proposal_type = str(proposal.get("type") or "")
    count = 0
    try:
        rejected = store.list_proposals(status="rejected", limit=80)
    except Exception:
        return 0
    for item in rejected:
        if str(item.get("type") or item.get("feedback", {}).get("proposal_type") or "") != proposal_type:
            continue
        feedback = item.get("feedback") if isinstance(item.get("feedback"), dict) else {}
        terms = {
            str(term).lower()
            for term in (feedback.get("trigger_terms") or [])
            if isinstance(term, str)
        }
        if not terms:
            terms = _proposal_similarity_signal(item)["terms"]
        if len(proposal_terms.intersection(terms)) >= 3:
            count += 1
    return count

def proposal_duplicate_warning(store: ExperienceStore | None, proposal: dict) -> str:
    if not store:
        return ""
    target_paths = {
        str(target) if str(target).startswith(".praxile/") else f".praxile/{target}"
        for target in proposal.get("target_files") or []
    }
    append_targets = {
        str(change.get("path")) if str(change.get("path")).startswith(".praxile/") else f".praxile/{change.get('path')}"
        for change in proposal.get("changes") or []
        if isinstance(change, dict) and change.get("operation", "write") == "append"
    }
    for target in proposal.get("target_files") or []:
        asset = store.get_asset(f".praxile/{target}" if not str(target).startswith(".praxile/") else str(target))
        normalized_target = str(target) if str(target).startswith(".praxile/") else f".praxile/{target}"
        if asset and asset.get("status") == "active" and normalized_target not in append_targets:
            return _format_duplicate_warning(store, proposal, str(asset["path"]), "target asset already exists and is active", "high")
    signal = _proposal_similarity_signal(proposal)
    for asset in _active_assets_for_duplicate_check(store):
        if asset.get("path") in target_paths:
            paragraph_reason = _same_target_append_duplicate_reason(proposal, asset, signal)
            if paragraph_reason:
                return _format_duplicate_warning(
                    store,
                    proposal,
                    str(asset["path"]),
                    f"same target append: {paragraph_reason}",
                    _duplicate_confidence_for_reason(paragraph_reason),
                )
            continue
        reason = _asset_similarity_reason(signal, asset)
        if reason:
            return _format_duplicate_warning(
                store,
                proposal,
                str(asset.get("path")),
                reason,
                _duplicate_confidence_for_reason(reason),
            )
    title = str(proposal.get("title") or "")
    if not title:
        return ""
    title_terms = _meaningful_proposal_terms(title)
    if not title_terms:
        return ""
    kinds = _proposal_retrieval_kinds(proposal)
    matches = store.retrieve(title, kinds=kinds, limit=3)
    for match in matches:
        matched_terms = {str(term).lower() for term in match.get("matched_terms") or []}
        meaningful_overlap = title_terms.intersection(matched_terms)
        if meaningful_overlap and match.get("path") not in target_paths:
            return _format_duplicate_warning(
                store,
                proposal,
                str(match.get("path")),
                f"title term overlap: {', '.join(sorted(meaningful_overlap)[:3])}",
                "possible",
            )
    return ""

def _format_duplicate_warning(
    store: ExperienceStore,
    proposal: dict,
    similar_path: str,
    reason: str,
    confidence: str,
) -> str:
    warning_id = _duplicate_warning_id(proposal, similar_path, reason)
    feedback = _duplicate_warning_feedback(store, warning_id)
    normalized = confidence if confidence in {"high", "medium", "possible"} else "possible"
    if feedback.get("ignored_or_accepted", 0) >= 2:
        normalized = "possible" if normalized != "high" else "medium"
        reason = f"{reason}; prior similar warnings were accepted/ignored {feedback['ignored_or_accepted']} time(s)"
    label = {
        "high": "High duplicate confidence",
        "medium": "Medium duplicate confidence",
        "possible": "Possible overlap",
    }[normalized]
    return f"{label}: similar_asset={similar_path}; reason={reason}; warning_id={warning_id}"

def _duplicate_warning_confidence(warning: str) -> str:
    if warning.startswith("High duplicate confidence"):
        return "high"
    if warning.startswith("Medium duplicate confidence"):
        return "medium"
    if warning.startswith("Possible overlap"):
        return "possible"
    return "medium" if warning else ""

def _duplicate_confidence_for_reason(reason: str) -> str:
    lower = reason.lower()
    if "same failure signature" in lower or "same command/file evidence" in lower or "paragraph fingerprint" in lower:
        return "high"
    if "affected file overlap" in lower or "content similarity" in lower or "paragraph term overlap" in lower:
        return "medium"
    return "possible"

def _duplicate_warning_id(proposal: dict, similar_path: str, reason: str) -> str:
    key = "|".join(
        [
            str(proposal.get("type") or ""),
            str(similar_path),
            str(reason),
            _proposal_feedback_query(proposal),
        ]
    )
    return stable_hash(key, length=16)

def _duplicate_warning_feedback(store: ExperienceStore, warning_id: str) -> dict[str, int]:
    path = store.paths.logs / "duplicate_warnings.jsonl"
    counts = {"ignored_or_accepted": 0, "merged_or_rejected": 0}
    if not path.exists():
        return counts
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return counts
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("warning_id") != warning_id:
            continue
        if event.get("user_action") in {"accepted_anyway", "ignored"}:
            counts["ignored_or_accepted"] += 1
        elif event.get("user_action") in {"rejected", "merged"}:
            counts["merged_or_rejected"] += 1
    return counts

def record_duplicate_warning_decision(store: ExperienceStore, proposal: dict, guidance: dict[str, str], user_action: str) -> None:
    warning = guidance.get("duplicate_warning") or ""
    if not warning:
        return
    match = re.search(r"warning_id=([A-Za-z0-9_-]+)", warning)
    warning_id = match.group(1) if match else stable_hash(warning, length=16)
    similar = re.search(r"similar_asset=([^;]+)", warning)
    reason = re.search(r"reason=([^;]+)", warning)
    append_jsonl(
        store.paths.logs / "duplicate_warnings.jsonl",
        {
            "event": "duplicate_warning_decision",
            "warning_id": warning_id,
            "proposal_id": proposal.get("proposal_id"),
            "proposal_type": proposal.get("type"),
            "similar_asset_path": similar.group(1).strip() if similar else None,
            "similarity_reason": reason.group(1).strip() if reason else warning,
            "user_action": user_action,
            "created_at": utc_now(),
        },
    )

def _proposal_retrieval_kinds(proposal: dict) -> list[str] | None:
    mapping = {
        "memory_update": ["memory"],
        "skill_create": ["skill"],
        "eval_case": ["eval"],
        "failure_pattern": ["failure"],
        "project_pattern": ["pattern"],
        "architecture_gate": ["rule"],
        "frozen_boundary": ["rule"],
        "harness_rule": ["rule"],
        "routing": ["rule"],
    }
    return mapping.get(str(proposal.get("type")))

def _active_assets_for_duplicate_check(store: ExperienceStore) -> list[dict]:
    assets: list[dict] = []
    for kind in ["memory", "failure", "pattern", "skill", "eval", "rule"]:
        try:
            assets.extend(store.list_assets(kind, include_inactive=False))
        except Exception:
            continue
    return assets

def _proposal_similarity_signal(proposal: dict) -> dict[str, set[str]]:
    text_parts: list[str] = [
        str(proposal.get("title") or ""),
        str(proposal.get("reason") or ""),
        str(proposal.get("trigger_reason") or ""),
        str(proposal.get("evidence_summary") or ""),
    ]
    text_parts.extend(str(value) for value in proposal.get("evidence") or [])
    text_parts.extend(str(value) for value in proposal.get("affected_files") or [])
    text_parts.extend(str(value) for value in proposal.get("target_files") or [])
    for change in proposal.get("changes") or []:
        if isinstance(change, dict):
            text_parts.append(str(change.get("path") or ""))
            text_parts.append(str(change.get("content") or ""))
            metadata = change.get("metadata")
            if isinstance(metadata, dict):
                text_parts.append(json.dumps(metadata, ensure_ascii=False))
    text = "\n".join(text_parts)
    return {
        "terms": _meaningful_proposal_terms(text),
        "failure_signatures": _extract_failure_signatures(text),
        "commands": _extract_command_signals(text),
        "paths": _extract_path_signals(text),
    }

def _asset_similarity_reason(signal: dict[str, set[str]], asset: dict) -> str:
    asset_text = " ".join(
        str(asset.get(key) or "")
        for key in ["path", "title", "summary", "tags", "source_task_id"]
    )
    asset_signal = {
        "terms": _meaningful_proposal_terms(asset_text),
        "failure_signatures": _extract_failure_signatures(asset_text),
        "commands": _extract_command_signals(asset_text),
        "paths": _extract_path_signals(asset_text),
    }
    signature_overlap = signal["failure_signatures"].intersection(asset_signal["failure_signatures"])
    if signature_overlap:
        return f"same failure signature: {', '.join(sorted(signature_overlap)[:2])}"
    command_overlap = signal["commands"].intersection(asset_signal["commands"])
    path_overlap = signal["paths"].intersection(asset_signal["paths"])
    if command_overlap and path_overlap:
        return f"same command/file evidence: {', '.join(sorted(command_overlap)[:2])}; {', '.join(sorted(path_overlap)[:2])}"
    if len(path_overlap) >= 2:
        return f"affected file overlap: {', '.join(sorted(path_overlap)[:3])}"
    term_overlap = signal["terms"].intersection(asset_signal["terms"])
    union = signal["terms"].union(asset_signal["terms"])
    similarity = len(term_overlap) / max(1, len(union))
    if len(term_overlap) >= 6 and similarity >= 0.45:
        return f"content similarity {similarity:.2f}: {', '.join(sorted(term_overlap)[:5])}"
    return ""

def _same_target_append_duplicate_reason(proposal: dict, asset: dict, signal: dict[str, set[str]]) -> str:
    if not any(change.get("operation", "write") == "append" for change in proposal.get("changes") or [] if isinstance(change, dict)):
        return ""
    asset_text = str(asset.get("summary") or "")
    asset_signatures = _extract_failure_signatures(asset_text)
    signature_overlap = signal["failure_signatures"].intersection(asset_signatures)
    if signature_overlap:
        return f"same failure signature: {', '.join(sorted(signature_overlap)[:2])}"
    proposed_fingerprints = _paragraph_fingerprints(_proposal_append_text(proposal))
    asset_fingerprints = _paragraph_fingerprints(asset_text)
    fingerprint_overlap = proposed_fingerprints.intersection(asset_fingerprints)
    if fingerprint_overlap:
        return f"paragraph fingerprint overlap: {', '.join(sorted(fingerprint_overlap)[:2])}"
    asset_terms = _meaningful_proposal_terms(asset_text)
    term_overlap = signal["terms"].intersection(asset_terms)
    if len(term_overlap) >= 6:
        return f"paragraph term overlap: {', '.join(sorted(term_overlap)[:5])}"
    return ""

def _proposal_append_text(proposal: dict) -> str:
    parts: list[str] = []
    for change in proposal.get("changes") or []:
        if isinstance(change, dict) and change.get("operation", "write") == "append":
            parts.append(str(change.get("content") or ""))
    return "\n\n".join(parts)

def _paragraph_fingerprints(text: str) -> set[str]:
    fingerprints: set[str] = set()
    for paragraph in re.split(r"\n\s*\n", str(text or "")):
        normalized = " ".join(sorted(_meaningful_proposal_terms(paragraph)))
        if len(normalized) < 24:
            continue
        fingerprints.add(stable_hash(normalized, length=12))
    return fingerprints

def _extract_failure_signatures(text: str) -> set[str]:
    signatures: set[str] = set()
    for line in str(text or "").splitlines():
        lower = line.lower()
        if "failure_signature" in lower:
            value = line.split(":", 1)[-1].strip(" `\"'")
            if value and value.lower() not in {"unknown", "not recorded"}:
                signatures.add(value.lower())
    for marker in ["assertionerror", "importerror", "modulenotfounderror", "permissionerror", "timeouterror", "valueerror"]:
        if marker in str(text or "").lower():
            signatures.add(marker)
    return signatures

def _extract_command_signals(text: str) -> set[str]:
    commands: set[str] = set()
    for match in re.findall(r"`([^`]*(?:pytest|npm|pnpm|yarn|go test|cargo test|python -m)[^`]*)`", str(text or ""), flags=re.I):
        commands.add(" ".join(match.lower().split()))
    for line in str(text or "").splitlines():
        lowered = line.strip().lower()
        if any(token in lowered for token in ["pytest", "npm test", "npm run", "go test", "cargo test", "python -m"]):
            commands.add(" ".join(lowered.strip("- `").split()))
    return {command for command in commands if len(command) > 5}

def _extract_path_signals(text: str) -> set[str]:
    paths = set()
    for match in re.findall(r"[\w./-]+\.(?:py|js|jsx|ts|tsx|go|rs|md|json)", str(text or "")):
        paths.add(match.strip("`.,:;()[]{}").lower())
    return {path for path in paths if "/" in path or "." in path}

def _meaningful_proposal_terms(text: str) -> set[str]:
    generic_terms = {
        "asset",
        "case",
        "create",
        "experience",
        "gate",
        "memory",
        "pattern",
        "proposal",
        "rule",
        "skill",
        "update",
    }
    return {
        term.lower()
        for term in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", text)
        if len(term) > 2 and term.lower() not in generic_terms
    }

def proposal_sort_key(proposal: dict) -> tuple[int, int, int, int, str]:
    risk_rank = {"high": 0, "medium": 1, "low": 2}.get(proposal.get("risk_level", "low"), 3)
    priority_rank = {"p0": 0, "p1": 1, "p2": 2}.get(str(proposal.get("priority", "p2")).lower(), 3)
    confidence_rank = {"high": 0, "medium": 1, "low": 2}.get(proposal_confidence_level(proposal), 3)
    type_rank = {
        "architecture_gate": 0,
        "frozen_boundary": 1,
        "harness_rule": 2,
        "failure_pattern": 3,
        "asset_deprecate": 4,
        "asset_merge": 4,
        "asset_supersede": 4,
        "asset_rewrite": 4,
        "asset_archive": 4,
        "asset_reactivate": 4,
        "skill_create": 5,
        "eval_case": 6,
        "memory_update": 7,
        "routing": 8,
    }.get(proposal.get("type"), 8)
    created = str(proposal.get("created_at") or proposal.get("updated_at") or "")
    return (risk_rank, priority_rank, confidence_rank, type_rank, created)

def proposal_confidence_level(proposal: dict) -> str:
    level = proposal.get("confidence_level")
    if level in {"high", "medium", "low"}:
        return str(level)
    try:
        confidence = float(proposal.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"

def proposal_older_than(proposal: dict, age: str) -> bool:
    delta = parse_age(age)
    created_at = proposal.get("created_at") or proposal.get("updated_at")
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(str(created_at))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created <= datetime.now(timezone.utc) - delta

def parse_age(value: str) -> timedelta:
    text = str(value or "").strip().lower()
    if len(text) < 2:
        raise ValueError("age must look like 30d, 12h, or 90m")
    unit = text[-1]
    try:
        amount = int(text[:-1])
    except ValueError as exc:
        raise ValueError("age must look like 30d, 12h, or 90m") from exc
    if amount < 0:
        raise ValueError("age must be non-negative")
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    raise ValueError("age unit must be d, h, or m")

def print_proposal_inbox_summary(proposals: list[dict]) -> None:
    counts = {"high": 0, "medium": 0, "low": 0}
    confidence = {"high": 0, "medium": 0, "low": 0}
    duplicates: dict[tuple[str, str], int] = {}
    for proposal in proposals:
        risk = proposal.get("risk_level", "low")
        if risk in counts:
            counts[risk] += 1
        level = proposal_confidence_level(proposal)
        confidence[level] = confidence.get(level, 0) + 1
        key = (str(proposal.get("type")), str(proposal.get("title", "")).strip().lower())
        duplicates[key] = duplicates.get(key, 0) + 1
    duplicate_count = sum(count - 1 for count in duplicates.values() if count > 1)
    print(f"Pending proposals: {len(proposals)}")
    print(f"- high risk: {counts['high']}")
    print(f"- medium risk: {counts['medium']}")
    print(f"- low risk: {counts['low']}")
    print(f"- low confidence: {confidence.get('low', 0)}")
    print(f"- possible duplicates: {duplicate_count}")
    print("\nRecommended:")
    if counts["high"]:
        print(f"- Review {counts['high']} high-risk proposal(s) first.")
    if confidence.get("low", 0):
        print("- Consider `praxile reject --low-confidence --reason \"too generic\"` for generic low-confidence items.")
    if counts["low"]:
        print("- Preview low-risk batch accept with `praxile accept --all-low-risk`, then apply with `--yes`.")
    if not proposals:
        print("- No pending proposal action needed.")

def print_pending_proposals(proposals: list[dict]) -> None:
    print("Pending proposals:")
    for index, proposal in enumerate(proposals, 1):
        targets = ", ".join(proposal.get("target_files", [])[:3])
        if len(proposal.get("target_files", [])) > 3:
            targets += ", ..."
        print(
            f"{index}. {proposal['proposal_id']} [{proposal['type']}] "
            f"priority={proposal.get('priority', 'p2')} "
            f"risk={proposal.get('risk_level', 'low')} "
            f"confidence={proposal_confidence_level(proposal)}"
        )
        print(f"   {proposal.get('title', '')}")
        print(f"   means: {proposal_plain_language(proposal)}")
        if proposal.get("evidence_summary"):
            print(f"   why: {proposal.get('evidence_summary')}")
        print(f"   targets: {targets or '(none)'}")

def review_pending_interactively(store: ExperienceStore, *, proposals: list[dict] | None = None) -> int:
    proposals = proposals if proposals is not None else filter_and_sort_proposals(store.list_proposals(status="pending"), argparse.Namespace())
    if not proposals:
        print("No pending proposals.")
        return 0
    print("Interactive proposal review")
    print("Commands: a=accept, r=reject, e=edit, s=skip, d=diff, q=quit")
    accepted = 0
    rejected = 0
    skipped = 0
    for index, proposal in enumerate(proposals, 1):
        print("\n" + "-" * 72)
        print(
            f"{index}/{len(proposals)} {proposal['proposal_id']} [{proposal['type']}] "
            f"priority={proposal.get('priority', 'p2')} "
            f"risk={proposal.get('risk_level', 'low')} confidence={proposal_confidence_level(proposal)}"
        )
        print(f"Title: {proposal.get('title', '')}")
        print(f"This means: {proposal_plain_language(proposal)}")
        print(f"Reason: {proposal.get('reason', '')}")
        guidance = proposal_review_guidance(store, proposal)
        print(f"Recommended action: {guidance['action']}")
        print(f"Why: {guidance['why']}")
        print(f"Will affect: {guidance['will_affect']}")
        print(f"Rollback: {guidance['rollback']}")
        if guidance.get("duplicate_warning"):
            print(f"Duplicate warning: {guidance['duplicate_warning']}")
        if guidance.get("feedback_note"):
            print(guidance["feedback_note"])
        if proposal.get("feedback_influence"):
            print(f"Feedback influence: {json.dumps(proposal['feedback_influence'], ensure_ascii=False)}")
        preview = proposal_governance_preview(proposal)
        if preview:
            for line in preview:
                print(line)
        if proposal.get("evidence_summary"):
            print(f"Evidence: {proposal.get('evidence_summary')}")
        if proposal.get("target_files"):
            print("Targets:")
            for path in proposal["target_files"]:
                print(f"- .praxile/{path}")
        while True:
            choice = safe_input("Review action [a/r/e/s/d/q]: ").strip().lower()
            if choice in {"a", "accept"}:
                record_duplicate_warning_decision(store, proposal, guidance, "accepted_anyway")
                applied = ProposalService(store).accept(proposal["proposal_id"], confirm=True)
                accepted += 1
                print(f"Accepted {applied['proposal_id']}: {applied['title']}")
                break
            if choice in {"r", "reject"}:
                reason = safe_input("Reject reason (optional): ").strip() or None
                record_duplicate_warning_decision(store, proposal, guidance, "rejected")
                rejected_proposal = ProposalService(store).reject(proposal["proposal_id"], reason=reason)
                rejected += 1
                print(f"Rejected {rejected_proposal['proposal_id']}: {rejected_proposal['title']}")
                break
            if choice in {"e", "edit"}:
                record_duplicate_warning_decision(store, proposal, guidance, "edited")
                proposal = edit_proposal_interactively(store, proposal)
                guidance = proposal_review_guidance(store, proposal)
                continue
            if choice in {"s", "skip", ""}:
                record_duplicate_warning_decision(store, proposal, guidance, "ignored")
                skipped += 1
                print("Skipped.")
                break
            if choice in {"d", "diff"}:
                show_text(proposal.get("diff", "") or "(no diff)", limit=16000, use_pager=True)
                continue
            if choice in {"q", "quit"}:
                print(f"Stopped. Accepted={accepted}, rejected={rejected}, skipped={skipped}.")
                return 0
            print("Choose a, r, e, s, d, or q.")
    print(f"\nDone. Accepted={accepted}, rejected={rejected}, skipped={skipped}.")
    return 0

def edit_proposal_interactively(store: ExperienceStore, proposal: dict) -> dict:
    editor = resolve_editor()
    if not editor:
        editor = safe_input("No editor configured. Enter editor command/path to edit this proposal, or leave blank to cancel: ").strip()
    if not editor:
        print("No editor selected; proposal was not changed.")
        return proposal
    edit_path = store.paths.state / "cache" / f"proposal-edit-{proposal['proposal_id']}.json"
    write_json(edit_path, proposal)
    try:
        timeout = int(os.environ.get("PRAXILE_EDITOR_TIMEOUT_SECONDS", "30") or "30")
        result = subprocess.run([*shlex.split(editor), str(edit_path)], check=False, timeout=max(1, timeout))
    except subprocess.TimeoutExpired:
        print(f"Editor timed out after {max(1, timeout)} seconds; proposal was not changed.")
        return proposal
    except OSError as exc:
        print(f"Editor failed: {exc}")
        return proposal
    if result.returncode != 0:
        print(f"Editor exited with status {result.returncode}; proposal was not changed.")
        return proposal
    try:
        edited = read_json(edit_path, {})
    except Exception as exc:
        print(f"Edited proposal is not valid JSON: {exc}")
        return proposal
    if not isinstance(edited, dict) or edited.get("proposal_id") != proposal.get("proposal_id"):
        print("Edited proposal must keep the same proposal_id; proposal was not changed.")
        return proposal
    edited["status"] = "pending"
    store.write_proposal(edited)
    print(f"Updated pending proposal {edited['proposal_id']}.")
    return edited

def resolve_editor() -> str | None:
    configured = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if configured:
        return configured
    candidates = ["notepad"] if os.name == "nt" else ["nano", "vi"]
    for candidate in candidates:
        if shutil.which(candidate):
            return candidate
    return None

def print_run_evolution_summary(trajectory: dict) -> None:
    summary = trajectory.get("evolution_summary") or {}
    loaded_counts = summary.get("used_asset_counts") or {}
    proposal_counts = summary.get("proposal_counts") or {}
    generation = summary.get("experience_generation") or trajectory.get("reward_report", {}).get("experience_generation") or {}
    print("Evolution summary:")
    if loaded_counts:
        loaded_text = ", ".join(f"{kind}={count}" for kind, count in sorted(loaded_counts.items()))
        print(f"- Used project experience: {loaded_text}")
    else:
        print("- Used project experience: none")
    if proposal_counts:
        proposal_text = ", ".join(f"{kind}={count}" for kind, count in sorted(proposal_counts.items()))
        print(f"- Produced proposals: {proposal_text}")
    else:
        print("- Produced proposals: none")
    if generation:
        print(f"- Experience generation: {generation.get('reason', 'not recorded')}")
    gate = summary.get("proposal_gate") or {}
    if gate:
        print(f"- Proposal gate: pending={gate.get('pending', 0)} suppressed={gate.get('suppressed', 0)}")
    silent = summary.get("silent_failure_signals") or []
    if silent:
        print(f"- Silent-failure signals: {len(silent)}")

def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts

def build_run_explanation(store: ExperienceStore, trajectory: dict) -> dict:
    task_id = trajectory.get("task_id")
    loaded_assets = trajectory.get("loaded_assets") or []
    if not loaded_assets and task_id:
        loaded_assets = [
            {
                "asset_id": item.get("path"),
                "asset_type": None,
                "kind": None,
                "path": item.get("path"),
                "score": item.get("score"),
                "matched_terms": item.get("matched_terms") or [],
                "matched_fields": item.get("matched_fields") or [],
                "why_loaded": item.get("why_loaded"),
                "used_in_prompt": item.get("used_in_prompt"),
                "outcome": item.get("outcome"),
            }
            for item in store.usage_for_task(task_id)
        ]
    hydrated_assets = []
    for item in loaded_assets:
        copy = dict(item)
        path = str(copy.get("path") or copy.get("asset_id") or "")
        if path:
            asset = store.get_asset(path)
            if asset:
                copy.setdefault("type", asset.get("type"))
                copy.setdefault("kind", asset.get("type"))
                copy.setdefault("title", asset.get("title"))
                copy["status"] = asset.get("status", copy.get("status", "active"))
                copy["replaced_by"] = asset.get("replaced_by", copy.get("replaced_by"))
                copy["deprecated_reason"] = asset.get("deprecated_reason", copy.get("deprecated_reason"))
                copy["superseded_reason"] = asset.get("superseded_reason", copy.get("superseded_reason"))
                copy["archived_reason"] = asset.get("archived_reason", copy.get("archived_reason"))
                copy["usage_count"] = asset.get("usage_count", copy.get("usage_count", 0))
                copy["positive_outcome_count"] = asset.get("positive_outcome_count", copy.get("positive_outcome_count", 0))
                copy["negative_outcome_count"] = asset.get("negative_outcome_count", copy.get("negative_outcome_count", 0))
                copy["last_used_at"] = asset.get("last_used_at", copy.get("last_used_at"))
        if copy.get("score_impact") is None:
            copy["score_impact"] = _score_impact(copy, store)
        copy.setdefault("attribution_level", _asset_attribution_level(copy))
        hydrated_assets.append(copy)
    produced = []
    for candidate in trajectory.get("experience_candidates", []):
        proposal = store.find_proposal(candidate.get("proposal_id")) or candidate
        produced.append(
            {
                "proposal_id": proposal.get("proposal_id"),
                "type": proposal.get("type"),
                "title": proposal.get("title"),
                "risk_level": proposal.get("risk_level"),
                "priority": proposal.get("priority"),
                "confidence_level": proposal_confidence_level(proposal),
                "evidence_summary": proposal.get("evidence_summary"),
                "proposal_gate": proposal.get("proposal_gate"),
                "target_files": proposal.get("target_files") or [],
                "status": proposal.get("status"),
            }
        )
    next_assets = [
        {
            "path": proposal.get("target_files", [""])[0],
            "type": proposal.get("type"),
            "title": proposal.get("title"),
        }
        for proposal in produced
        if proposal.get("status") == "accepted" and proposal.get("target_files")
    ]
    return {
        "task_id": task_id,
        "user_task": trajectory.get("user_task"),
        "result": trajectory.get("result"),
        "spec_context": trajectory.get("spec_context") or {},
        "spec_compliance": trajectory.get("spec_compliance") or {},
        "silent_failure_signals": trajectory.get("silent_failure_signals") or [],
        "proposal_gate_summary": trajectory.get("proposal_gate_summary") or {},
        "suppressed_experience_candidates": trajectory.get("suppressed_experience_candidates") or [],
        "executors": trajectory.get("executors") or [],
        "parallel_readonly_exploration": trajectory.get("parallel_readonly_exploration") or {},
        "reward": trajectory.get("reward_report", {}),
        "used": hydrated_assets,
        "produced": produced,
        "next_similar_task_will_likely_load": next_assets,
        "review_command": f"praxile review --source-run {task_id}",
    }

def print_run_explanation(explanation: dict) -> None:
    print("Self-Evolution Report")
    print(f"Task: {explanation.get('task_id')}")
    print(f"User task: {explanation.get('user_task')}")
    result = explanation.get("result") or {}
    print(f"Status: {result.get('status')}")
    print(f"Summary: {result.get('summary')}")
    reward = explanation.get("reward") or {}
    final_reward = reward.get("final_reward") or {}
    if final_reward:
        print(f"Reward: {final_reward.get('score')} ({final_reward.get('mode')})")
        effective = final_reward.get("effective_weights") or {}
        if effective:
            print(
                "Reward sources: "
                f"objective={effective.get('objective', 0)} "
                f"user_feedback={effective.get('user_feedback', 0)} "
                f"llm_judge={effective.get('llm_judge', 0)}"
            )
    user_feedback = reward.get("user_feedback_reward") or {}
    if user_feedback.get("active"):
        print(
            f"User feedback: score={user_feedback.get('score')} "
            f"positive={user_feedback.get('positive_count', 0)} negative={user_feedback.get('negative_count', 0)}"
        )
    spec_context = explanation.get("spec_context") or {}
    if spec_context.get("enabled"):
        print("\n0. Spec and governance context")
        print(f"- Spec quality: {spec_context.get('quality_label')} ({spec_context.get('quality_score')})")
        for path in spec_context.get("spec_files") or []:
            print(f"- Spec file: {path}")
        for path in spec_context.get("constitution_files") or []:
            print(f"- Constitution: {path}")
        if spec_context.get("missing_sections"):
            print("- Missing sections: " + ", ".join(spec_context.get("missing_sections") or []))
    spec_compliance = explanation.get("spec_compliance") or {}
    if spec_compliance:
        print("\n0a. Spec compliance")
        print(f"- Status: {spec_compliance.get('status')} ({spec_compliance.get('score')})")
        if spec_compliance.get("missing"):
            print(f"- Missing acceptance items: {len(spec_compliance.get('missing') or [])}")
        if spec_compliance.get("violations"):
            print(f"- Violations: {len(spec_compliance.get('violations') or [])}")
    silent_signals = explanation.get("silent_failure_signals") or []
    if silent_signals:
        print("\n0b. Silent-failure signals")
        for signal in silent_signals:
            print(f"- {signal.get('type')} risk={signal.get('risk')}: {signal.get('reason')}")
    gate_summary = explanation.get("proposal_gate_summary") or {}
    if gate_summary:
        print("\n0c. Proposal gate")
        print(
            f"- generated={gate_summary.get('generated', 0)} "
            f"pending={gate_summary.get('pending', 0)} suppressed={gate_summary.get('suppressed', 0)}"
        )
    executors = explanation.get("executors") or []
    if executors:
        print("\n0d. Executors")
        for executor in executors:
            print(
                f"- {executor.get('executor_id')} "
                f"kind={executor.get('kind')} role={executor.get('role')}"
            )
    exploration = explanation.get("parallel_readonly_exploration") or {}
    if exploration.get("enabled"):
        print(
            f"- Parallel read-only exploration: status={exploration.get('status')} "
            f"actions={exploration.get('action_count')}"
        )
    print("\n1. Why these experiences were loaded")
    used = explanation.get("used") or []
    if not used:
        print("- This run had no accepted project experience context.")
    for item in used:
        print(
            f"- {_display_state_path(item.get('path'))} "
            f"final_score={item.get('final_score', item.get('score'))} "
            f"usage={item.get('usage_count', 0)} "
            f"positive={item.get('positive_outcome_count', 0)} "
            f"negative={item.get('negative_outcome_count', 0)} "
            f"attribution={item.get('attribution_level')}"
        )
        print(
            f"  impact: loaded={'yes' if item.get('used_in_prompt', True) else 'no'} "
            f"referenced={'yes' if item.get('referenced') else 'no'} "
            f"used_explicitly={'yes' if item.get('used_explicitly') else 'no'}"
        )
        if item.get("why_loaded"):
            print(f"  why: {item.get('why_loaded')}")
        matched = item.get("matched_terms") or []
        if matched:
            print(f"  matched terms: {', '.join(str(value) for value in matched[:8])}")
        if item.get("score_impact") is not None:
            print(f"  score impact from usage feedback: {item.get('score_impact')}")
        semantic_attr = item.get("semantic_attribution") if isinstance(item.get("semantic_attribution"), dict) else {}
        if semantic_attr:
            print(
                f"  semantic attribution: level={semantic_attr.get('attribution_level')} "
                f"confidence={semantic_attr.get('confidence')}"
            )
            if semantic_attr.get("reason"):
                print(f"  semantic reason: {semantic_attr.get('reason')}")
            for evidence in (semantic_attr.get("evidence") or [])[:3]:
                print(f"  semantic evidence: {evidence}")
        status = item.get("status") or "active"
        if status != "active":
            lifecycle = f"  lifecycle: {status}"
            if item.get("replaced_by"):
                lifecycle += f" replaced_by={item.get('replaced_by')}"
            print(lifecycle)
            for key in ["deprecated_reason", "superseded_reason", "archived_reason"]:
                if item.get(key):
                    print(f"  {key}: {item.get(key)}")
    print("\n2. What this run learned")
    produced = explanation.get("produced") or []
    if not produced:
        generation = (explanation.get("reward") or {}).get("experience_generation") or {}
        reason = generation.get("reason") or "no experience proposals were generated"
        print(f"- {reason}")
    for proposal in produced:
        print(
            f"- {proposal.get('proposal_id')} [{proposal.get('type')}] "
            f"risk={proposal.get('risk_level')} confidence={proposal.get('confidence_level')} "
            f"status={proposal.get('status')}"
        )
        print(f"  {proposal.get('title')}")
        if proposal.get("evidence_summary"):
            print(f"  evidence: {proposal.get('evidence_summary')}")
        gate = proposal.get("proposal_gate") or {}
        if gate:
            print(f"  gate: {gate.get('decision')} ({'; '.join(gate.get('reasons') or [])})")
    suppressed = explanation.get("suppressed_experience_candidates") or []
    if suppressed:
        print("\nSuppressed weak candidates:")
        for item in suppressed:
            gate = item.get("proposal_gate") or {}
            print(f"- {item.get('proposal_id')} [{item.get('type')}] {item.get('title')}")
            for reason in gate.get("suppressed_reasons") or []:
                print(f"  suppressed: {reason}")
    print("\n3. What will change next time after approval")
    next_assets = explanation.get("next_similar_task_will_likely_load") or []
    if not next_assets:
        print("- Nothing durable changes until pending proposals are accepted.")
    for item in next_assets:
        print(f"- {_display_state_path(item.get('path'))} ({item.get('type')})")
    print(f"\nReview command: {explanation.get('review_command')}")

def _display_state_path(path: object) -> str:
    text = str(path or "")
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}" if text else ".praxile/(unknown)"

def _score_impact(item: dict, store: ExperienceStore | None = None) -> float | None:
    if item.get("score_impact") is not None:
        return item.get("score_impact")
    parts = [item.get("usage_score"), item.get("positive_boost")]
    penalties = [item.get("negative_penalty"), item.get("staleness_penalty")]
    numeric_parts = [float(value) for value in parts if isinstance(value, (int, float))]
    numeric_penalties = [float(value) for value in penalties if isinstance(value, (int, float))]
    if not numeric_parts and not numeric_penalties:
        config = getattr(store, "config", None)
        try:
            usage = int(item.get("usage_count") or 0)
            positive = int(item.get("positive_outcome_count") or 0)
            negative = int(item.get("negative_outcome_count") or 0)
        except (TypeError, ValueError):
            usage = positive = negative = 0
        usage_weight = float(config.get("retrieval", "usage_log_weight", default=0.02) if config else 0.02)
        positive_weight = float(config.get("retrieval", "positive_outcome_weight", default=0.10) if config else 0.10)
        negative_weight = float(config.get("retrieval", "negative_outcome_weight", default=0.20) if config else 0.20)
        value = math.log1p(max(0, usage)) * usage_weight + max(0, positive) * positive_weight - max(0, negative) * negative_weight
        if value:
            return round(value, 4)
    if not numeric_parts and not numeric_penalties:
        return None
    return round(sum(numeric_parts) - sum(numeric_penalties), 4)

def _asset_attribution_level(asset: dict) -> str:
    try:
        helpful = int(asset.get("user_helpful_count") or asset.get("helpful_count") or asset.get("positive_feedback_count") or 0)
        harmful = int(asset.get("user_harmful_count") or asset.get("harmful_count") or asset.get("negative_feedback_count") or 0)
        positive = int(asset.get("positive_outcome_count") or 0)
        negative = int(asset.get("negative_outcome_count") or 0)
    except (TypeError, ValueError):
        helpful = harmful = positive = negative = 0
    semantic = asset.get("semantic_attribution") if isinstance(asset.get("semantic_attribution"), dict) else {}
    if semantic.get("attribution_level"):
        return _normalize_display_attribution(semantic.get("attribution_level"))
    if asset.get("used_explicitly"):
        if negative > positive:
            return "harmful"
        if positive > 0:
            return "strong_positive"
        return "referenced"
    if asset.get("referenced"):
        if negative > positive:
            return "weak_negative"
        if positive > 0:
            return "weak_positive"
        return "referenced"
    if helpful > 0:
        return "strong_positive"
    if harmful > 0:
        return "harmful"
    if positive > 0 and negative == 0:
        return "weak_positive"
    if negative > 0 and positive == 0:
        return "weak_negative"
    if positive > 0 and negative > 0:
        return "mixed"
    if asset.get("path") or asset.get("asset_id"):
        return "loaded_only"
    return "unknown"

def _normalize_display_attribution(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    aliases = {
        "medium_positive": "weak_positive",
        "medium_negative": "weak_negative",
        "strong_negative": "harmful",
        "user_helpful": "strong_positive",
        "user_harmful": "harmful",
    }
    return aliases.get(text, text)

def print_trajectory(trajectory: dict, *, use_pager: bool = False) -> None:
    print("Task Summary")
    print(f"Task: {trajectory['task_id']}")
    print(f"User task: {trajectory.get('user_task')}")
    print(f"Status: {trajectory.get('result', {}).get('status')}")
    if trajectory.get("dry_run"):
        print("Mode: dry-run")
    print(f"Summary: {trajectory.get('result', {}).get('summary')}")
    print(f"Started: {trajectory.get('start_time')}")
    print(f"Ended: {trajectory.get('end_time')}")

    analysis = trajectory.get("task_analysis") or {}
    if analysis:
        print("\nTask Analysis:")
        print(f"- type: {analysis.get('task_type')}")
        print(f"- risk: {analysis.get('risk_level')}")
        print(f"- architecture gate required: {analysis.get('architecture_gate_required')}")
        print(f"- UI human review required: {analysis.get('ui_human_review_required')}")
        print(f"- privacy sensitive: {analysis.get('privacy_sensitive')}")
        if analysis.get("reasons"):
            print("- reasons:")
            for reason in analysis["reasons"]:
                print(f"  - {reason}")

    report = trajectory.get("reward_report", {})
    print("\nReward Report:")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    print("\nLoaded Experience:")
    loaded_assets = trajectory.get("loaded_assets") or []
    if loaded_assets:
        for item in loaded_assets:
            print(f"- {item.get('path')} score={item.get('score')} why={item.get('why_loaded')}")
    else:
        print("- none")

    diff = trajectory.get("diff_summary", {}).get("diff", "")
    if diff:
        print("\nDiff Summary:")
        show_text(diff, limit=12000, use_pager=use_pager)

    safety_events = [
        action
        for action in trajectory.get("actions", [])
        if action.get("status") == "blocked" or action.get("observation", {}).get("risk_level") in {"medium", "high"}
    ]
    print("\nSafety Events:")
    if safety_events:
        for action in safety_events:
            output = action.get("observation", {}).get("output", "")
            print(f"- #{action.get('step')} {action.get('action_type')} -> {action.get('status')}: {shorten(output, 240)}")
    else:
        print("- none")

    print("\nSearch Completeness:")
    search_summary = summarize_search_completeness(trajectory)
    for line in search_summary:
        print(f"- {line}")

    print("\nActions:")
    for action in trajectory.get("actions", []):
        output = action.get("observation", {}).get("output", "")
        print(f"- #{action['step']} {action['action_type']} -> {action['status']}")
        if output:
            print(indent_block(shorten(output, 600), "    "))

    proposals = trajectory.get("experience_candidates", [])
    print("\nGenerated Proposals:")
    if proposals:
        for proposal in proposals:
            print(
                f"- {proposal['proposal_id']} [{proposal['type']}] "
                f"risk={proposal.get('risk_level')} confidence={proposal.get('confidence_level', proposal.get('confidence'))} "
                f"{proposal['title']}"
            )
    else:
        print("- none")

    print("\nRecommended Next Action:")
    print(f"- {recommended_next_action(trajectory)}")

def print_proposal(proposal: dict, *, use_pager: bool = False) -> None:
    print(f"Proposal: {proposal['proposal_id']}")
    print(f"Type: {proposal['type']}")
    print(f"Status: {proposal['status']}")
    print(f"Risk: {proposal['risk_level']}")
    print(f"Priority: {proposal.get('priority', 'p2')}")
    print(f"Confidence: {proposal_confidence_level(proposal)} ({proposal.get('confidence', 'n/a')})")
    print(f"Title: {proposal['title']}")
    print(f"This means: {proposal_plain_language(proposal)}")
    guidance = proposal_review_guidance(None, proposal)
    print(f"Recommended action: {guidance['action']}")
    print(f"Why: {guidance['why']}")
    print(f"Will affect: {guidance['will_affect']}")
    print(f"Rollback: {guidance['rollback']}")
    preview = proposal_governance_preview(proposal)
    if preview:
        for line in preview:
            print(line)
    print(f"Reason: {proposal['reason']}")
    if proposal.get("trigger_reason"):
        print(f"Trigger: {proposal['trigger_reason']}")
    if proposal.get("evidence_summary"):
        print(f"Evidence summary: {proposal['evidence_summary']}")
    if proposal.get("applicability_scope"):
        print(f"Scope: {proposal['applicability_scope']}")
    if proposal.get("anti_scope"):
        print(f"Anti-scope: {proposal['anti_scope']}")
    if proposal.get("future_applicability"):
        print(f"Future applicability: {proposal['future_applicability']}")
    if proposal.get("generated_by"):
        print(f"Generated by: {proposal['generated_by']}")
    if proposal.get("feedback_influence"):
        print(f"Feedback influence: {json.dumps(proposal['feedback_influence'], ensure_ascii=False)}")
    if proposal.get("pattern_score") is not None:
        print(f"Pattern score: {proposal.get('pattern_score')}")
    if proposal.get("confidence_rationale"):
        print(f"Confidence rationale: {proposal.get('confidence_rationale')}")
    if proposal.get("match_reasons"):
        print(f"Match reasons: {', '.join(str(item) for item in proposal.get('match_reasons', [])[:8])}")
    if proposal.get("semantic_reasons"):
        print("Semantic judge notes:")
        for reason in proposal.get("semantic_reasons", [])[:5]:
            print(f"- {reason}")
    if proposal.get("counterexamples"):
        print("Counterexamples:")
        for item in proposal.get("counterexamples", [])[:5]:
            if isinstance(item, dict):
                print(f"- {item.get('type')}: {item.get('reason')} ({item.get('confidence_delta')})")
            else:
                print(f"- {item}")
    if proposal.get("llm_judge"):
        judge = proposal.get("llm_judge") or {}
        print(
            f"LLM judge: score={judge.get('score')} overgeneralization_risk={judge.get('overgeneralization_risk')} "
            f"recommended={judge.get('recommended_action')}"
        )
        for reason in judge.get("reasons", [])[:5]:
            print(f"- judge: {reason}")
    if proposal.get("source"):
        print(f"Source Task: {proposal['source'].get('type')}:{proposal['source'].get('task_id')}")
    if proposal.get("affected_files"):
        print("Affected files:")
        for path in proposal["affected_files"]:
            print(f"- {path}")
    if proposal.get("evidence"):
        print("Evidence:")
        for item in proposal["evidence"]:
            if isinstance(item, dict):
                print(f"- {item.get('summary', item)}")
            else:
                print(f"- {item}")
    print("Targets:")
    for path in proposal.get("target_files", []):
        print(f"- .praxile/{path}")
    print("Rollback Plan:")
    if proposal.get("status") == "accepted":
        print(f"- Use `praxile rollback {proposal['proposal_id']}` to restore applied .praxile asset changes.")
    else:
        print("- No durable asset change is applied until this proposal is accepted.")
    if proposal.get("diff"):
        print("\nDiff:")
        show_text(proposal["diff"], limit=16000, use_pager=use_pager)

def show_text(text: str, *, limit: int, use_pager: bool = False) -> None:
    if use_pager and sys.stdout.isatty():
        pager = os.environ.get("PAGER", "less -R")
        try:
            timeout = int(os.environ.get("PRAXILE_PAGER_TIMEOUT_SECONDS", "30") or "30")
            subprocess.run(shlex.split(pager), input=text, text=True, check=False, timeout=max(1, timeout))
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    print(shorten(text, limit))

def summarize_search_completeness(trajectory: dict) -> list[str]:
    searches = [action for action in trajectory.get("actions", []) if action.get("action_type") == "search"]
    if not searches:
        return ["no search actions recorded"]
    backends = sorted(
        {
            action.get("observation", {}).get("data", {}).get("backend")
            for action in searches
            if action.get("observation", {}).get("data", {}).get("backend")
        }
    )
    skipped = 0
    errors = 0
    protected = 0
    truncated = 0
    for action in searches:
        data = action.get("observation", {}).get("data", {})
        skipped += len(data.get("skipped") or [])
        errors += len(data.get("errors") or [])
        protected += int(data.get("protected_skipped_count") or 0)
        truncated += 1 if data.get("truncated") else 0
    lines = [
        f"search actions: {len(searches)}",
        f"backend(s): {', '.join(backends) if backends else 'unknown'}",
        f"skipped files recorded: {skipped}",
        f"errors recorded: {errors}",
        f"protected files skipped: {protected}",
        f"truncated searches: {truncated}",
    ]
    if skipped or errors or truncated:
        lines.append("coverage may be incomplete; inspect action observations before relying on absence of matches")
    return lines

def recommended_next_action(trajectory: dict) -> str:
    result_status = trajectory.get("result", {}).get("status")
    actions = trajectory.get("actions", [])
    has_gate = any(action.get("action_type") == "architecture_gate" for action in actions)
    report = trajectory.get("reward_report", {})
    proposals = trajectory.get("experience_candidates", [])
    tests_passed = report.get("regression_passed")
    if has_gate:
        return "Review the architecture-gate proposal; start implementation only as a new explicit task after approval."
    if tests_passed is False:
        return "Fix failing verification before accepting learning proposals as durable experience."
    if trajectory.get("dry_run"):
        return "Inspect the dry-run trajectory, then rerun without --dry-run if the plan looks safe."
    if proposals:
        return "Inspect proposal diffs and accept only the memory/skill/eval/rule updates you want to keep."
    if result_status == "completed":
        return "Run any missing project verification and keep the trajectory for audit."
    return "Review blocked or failed actions and decide whether to adjust config, retry, or stop."


__all__ = [name for name in globals() if not name.startswith("__")]
