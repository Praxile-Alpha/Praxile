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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit import (
    audit_json,
    build_asset_audit,
    build_project_audit_bundle,
    build_project_audit_check,
    build_proposal_audit,
    build_run_audit,
    format_audit_report,
)
from .channels import ChannelSystem
from .consolidation import ConsolidationEngine
from .config import Config, ProjectPaths, find_project_root
from .environment import FileSystemEnv, GitEnv, ProjectEnv, ShellEnv, TestEnv
from .evolution import EvolutionEngine
from .feedback import FeedbackSemanticClassifier, build_feedback, extract_feedback_intents
from .gateway import serve_gateway
from .inspector import inspect_project
from .interop import format_interop_policy, interop_policy
from .memory import MemorySystem
from .model import ModelRouter
from .project_map import build_project_map
from .reward import RewardEngine
from .runtime import AgentRuntime
from .security import SafetyPolicy
from .skills import SkillSystem
from .specs import check_spec_file, format_spec_check, format_spec_compliance, verify_spec_compliance
from .store import ExperienceStore
from .terminal import TerminalSession
from .tools import ToolRegistry
from .trajectory import TrajectoryLogger
from .utils import append_jsonl, indent_block, read_json, safe_input, shorten, stable_hash, unified_diff, utc_now, write_json
from .workspace import WorkspaceManager, workspace_diff_summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = find_project_root(Path(args.project).resolve()) if getattr(args, "project", None) else find_project_root(Path.cwd())
    if not hasattr(args, "func"):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args, project_root)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="praxile",
        description="Praxile: governed experience harness for AI coding",
    )
    parser.add_argument("--project", help="Project root. Defaults to nearest .praxile/.git/current directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize .praxile in the project")
    p_init.add_argument("--force", action="store_true", help="Regenerate default config and seed files")
    p_init.add_argument("--no-detect", action="store_true", help="Skip project stack and verification command detection")
    p_init.add_argument("--test-command", action="append", default=[], help="Seed runtime.default_test_commands")
    p_init.add_argument("--wizard", action="store_true", help="Run step-by-step local setup after initialization")
    p_init.set_defaults(func=cmd_init)

    p_setup = sub.add_parser("setup", help="Step-by-step model and channel configuration")
    p_setup.add_argument(
        "--provider",
        choices=["none", "ollama", "openai-compatible", "anthropic"],
        default=None,
        help="Model provider to configure. Omit for interactive setup.",
    )
    p_setup.add_argument("--model", default=None, help="Model name for the selected provider")
    p_setup.add_argument("--base-url", default=None, help="Provider base URL")
    p_setup.add_argument("--api-key-env", default=None, help="Environment variable containing the provider API key")
    p_setup.add_argument("--channel", choices=["none", "telegram", "discord"], default=None, help="Optional channel binding")
    p_setup.add_argument("--channel-id", default=None, help="Telegram chat ID or Discord channel ID")
    p_setup.add_argument("--guild-id", default=None, help="Discord guild/server ID")
    p_setup.add_argument("--token-env", default=None, help="Environment variable containing the channel bot token")
    p_setup.add_argument("--mode", choices=["notify", "task", "bidirectional"], default="notify")
    p_setup.set_defaults(func=cmd_setup)

    p_demo = sub.add_parser("demo", help="Create and run a local governed-experience demo project")
    p_demo.add_argument("--path", default=None, help="Directory for the demo project. Defaults to a new temp directory.")
    p_demo.add_argument("--accept-first", action="store_true", help="Accept the first low-risk memory proposal in the demo project")
    p_demo.add_argument("--force", action="store_true", help="Allow Praxile to overwrite demo-owned files in a non-empty demo directory")
    p_demo.add_argument("--fast", action="store_true", help="Run a synthetic no-test demo of trajectory/reward/proposal/retrieval")
    p_demo.add_argument("--show-files", action="store_true", help="Print files generated by the demo run")
    p_demo.set_defaults(func=cmd_demo)

    p_run = sub.add_parser("run", help="Run an agent task")
    p_run.add_argument("task", nargs="?", help="User task")
    p_run.add_argument("--test-command", action="append", default=[], help="Safe test/lint/build command to run after edits")
    p_run.add_argument("--max-steps", type=int, default=None, help="Maximum model action steps")
    p_run.add_argument("--dry-run", action="store_true", help="Analyze/plan without editing files or running commands")
    p_run.add_argument("--resume", default=None, help="Resume from a .praxile/checkpoints/<TASK_ID>.json checkpoint")
    p_run.add_argument("--model-default", default=None, help="Temporary default_model route override, e.g. openai:gpt-4o")
    p_run.add_argument("--model-planning", default=None, help="Temporary planning_model route override")
    p_run.add_argument("--model-coding", default=None, help="Temporary coding_model route override")
    p_run.add_argument("--model-evolution", default=None, help="Temporary evolution_model route override")
    p_run.add_argument("--model-private", default=None, help="Temporary private_model route override")
    p_run.add_argument("--model-cheap", default=None, help="Temporary cheap_model route override")
    p_run.add_argument("--spec", action="append", default=[], help="Attach a spec/plan/tasks Markdown file to the run")
    p_run.add_argument(
        "--parallel-readonly-explore",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run a concurrent read-only exploration batch before model action planning",
    )
    p_run.add_argument(
        "--workspace-mode",
        choices=["in-place", "copy", "git-worktree"],
        default=None,
        help="Run in the project directly or in an isolated per-task workspace",
    )
    p_run.add_argument(
        "--keep-workspace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Keep or remove an isolated workspace after importing trajectory/proposals",
    )
    p_run.set_defaults(func=cmd_run)

    p_review = sub.add_parser(
        "review",
        help="Review latest or specified task/proposal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Decision guide:\n"
            "  accept           low-risk, high-confidence local memory/eval/failure cleanup with concrete evidence\n"
            "  inspect          architecture, routing, frozen-boundary, harness-rule, or medium/high-risk proposals\n"
            "  reject_or_edit   low-confidence or generic proposals that need human cleanup\n"
            "  inspect_duplicate overlapping active assets that may pollute retrieval"
        ),
    )
    p_review.add_argument("id", nargs="?", help="Task ID or proposal ID")
    p_review.add_argument("--pending", action="store_true", help="List all pending experience proposals")
    p_review.add_argument("-i", "--interactive", action="store_true", help="Step through pending proposals")
    p_review.add_argument("--pager", action="store_true", help="Open long diffs in $PAGER when stdout is a terminal")
    p_review.add_argument("--type", dest="proposal_type", default=None, help="Filter pending proposals by type")
    p_review.add_argument("--risk", choices=["low", "medium", "high"], default=None, help="Filter pending proposals by risk")
    p_review.add_argument(
        "--confidence",
        choices=["low", "medium", "high"],
        default=None,
        help="Filter pending proposals by confidence level",
    )
    p_review.add_argument("--source-run", default=None, help="Filter pending proposals by source task/run ID")
    p_review.add_argument("--older-than", default=None, help="Filter pending proposals older than an age such as 30d or 12h")
    p_review.add_argument("--summary", action="store_true", help="Show proposal inbox counts and recommended next action")
    p_review.add_argument("--high-risk", action="store_true", help="Shortcut for --risk high")
    p_review.add_argument(
        "--recommended",
        choices=["accept", "inspect", "reject_or_edit", "inspect_duplicate"],
        default=None,
        help="Filter interactive proposals by recommended action",
    )
    p_review.set_defaults(func=cmd_review)

    p_accept = sub.add_parser("accept", help="Accept a pending experience proposal")
    p_accept.add_argument("proposal_id", nargs="?", help="Proposal ID from `praxile review`.")
    p_accept.add_argument("--all-low-risk", action="store_true", help="Accept all pending low-risk proposals")
    p_accept.add_argument("--dry-run", action="store_true", help="Preview batch accept without writing durable assets")
    p_accept.add_argument("--yes", action="store_true", help="Apply batch accept without interactive confirmation")
    p_accept.add_argument("--limit", type=int, default=None, help="Maximum batch proposals to accept")
    p_accept.set_defaults(func=cmd_accept)

    p_reject = sub.add_parser("reject", help="Reject a pending experience proposal")
    p_reject.add_argument("proposal_id", nargs="?", help="Proposal ID from `praxile review`.")
    p_reject.add_argument("--reason", default=None, help="Optional rejection reason")
    p_reject.add_argument("--low-confidence", action="store_true", help="Reject all pending low-confidence proposals")
    p_reject.add_argument("--older-than", default=None, help="Reject pending proposals older than an age such as 30d or 12h")
    p_reject.set_defaults(func=cmd_reject)

    p_history = sub.add_parser("history", help="List task trajectory history")
    p_history.add_argument("--limit", type=int, default=20)
    p_history.add_argument("--offset", type=int, default=0, help="Skip this many newest rows")
    p_history.add_argument("--status", default=None, help="Filter by result status")
    p_history.add_argument("--query", default=None, help="Filter by task text substring")
    p_history.set_defaults(func=cmd_history)

    p_explain = sub.add_parser("explain", help="Explain a run's loaded experience and generated proposals")
    p_explain.add_argument("id", nargs="?", default="latest", help="Run/task ID, or latest")
    p_explain.add_argument("--spec", action="store_true", help="Highlight spec context, proposal gate, and silent-failure signals")
    p_explain.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_explain.set_defaults(func=cmd_explain)

    p_graph = sub.add_parser("graph", help="Experience graph commands")
    graph_sub = p_graph.add_subparsers(dest="graph_command", required=True)
    p_graph_status = graph_sub.add_parser("status", help="Show experience graph index status")
    p_graph_status.add_argument("--rebuild", action="store_true", help="Rebuild graph before showing status")
    p_graph_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_graph_status.set_defaults(func=cmd_graph_status)
    p_graph_rebuild = graph_sub.add_parser("rebuild", help="Rebuild the experience graph index")
    p_graph_rebuild.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_graph_rebuild.set_defaults(func=cmd_graph_rebuild)
    p_graph_explain = graph_sub.add_parser("explain", help="Explain relationships around a run, proposal, spec, or asset")
    p_graph_explain.add_argument("ref", help="Node ID or ref, e.g. .praxile/memory/project.md, prop_x, task_x, spec.md")
    p_graph_explain.add_argument("--depth", type=int, default=1, help="Traversal depth, max 4")
    p_graph_explain.add_argument("--limit", type=int, default=100, help="Maximum edges to show")
    p_graph_explain.add_argument("--rebuild", action="store_true", help="Rebuild graph before explaining")
    p_graph_explain.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_graph_explain.set_defaults(func=cmd_graph_explain)
    p_graph_trace = graph_sub.add_parser("trace", help="Trace one proposal's source and target assets")
    p_graph_trace.add_argument("proposal_id")
    p_graph_trace.add_argument("--depth", type=int, default=2)
    p_graph_trace.add_argument("--limit", type=int, default=120)
    p_graph_trace.add_argument("--rebuild", action="store_true")
    p_graph_trace.add_argument("--json", action="store_true")
    p_graph_trace.set_defaults(func=cmd_graph_trace)
    p_graph_impact = graph_sub.add_parser("impact", help="Show runs/proposals/assets related to a spec")
    p_graph_impact.add_argument("spec_id")
    p_graph_impact.add_argument("--depth", type=int, default=2)
    p_graph_impact.add_argument("--limit", type=int, default=120)
    p_graph_impact.add_argument("--rebuild", action="store_true")
    p_graph_impact.add_argument("--json", action="store_true")
    p_graph_impact.set_defaults(func=cmd_graph_impact)

    p_audit = sub.add_parser("audit", help="Export audit chains for runs, proposals, and experience assets")
    audit_sub = p_audit.add_subparsers(dest="audit_command", required=True)
    p_audit_run = audit_sub.add_parser("run", help="Export one run's decision, reward, evidence, and proposal chain")
    p_audit_run.add_argument("id", nargs="?", default="latest", help="Run/task ID, or latest")
    p_audit_run.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit_run.add_argument("--output", default=None, help="Write full JSON audit report to this path")
    p_audit_run.add_argument("--rebuild-graph", action="store_true", help="Rebuild experience graph before audit")
    p_audit_run.add_argument("--redaction", choices=["standard", "strict", "none"], default="standard", help="Redaction profile for audit JSON")
    p_audit_run.set_defaults(func=cmd_audit_run)
    p_audit_asset = audit_sub.add_parser("asset", help="Export one asset's source, lifecycle, usage, and proposal chain")
    p_audit_asset.add_argument("path", help="Project-local asset path, with or without .praxile/")
    p_audit_asset.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit_asset.add_argument("--output", default=None, help="Write full JSON audit report to this path")
    p_audit_asset.add_argument("--rebuild-graph", action="store_true", help="Rebuild experience graph before audit")
    p_audit_asset.add_argument("--redaction", choices=["standard", "strict", "none"], default="standard", help="Redaction profile for audit JSON")
    p_audit_asset.set_defaults(func=cmd_audit_asset)
    p_audit_proposal = audit_sub.add_parser("proposal", help="Export one proposal's evidence, review, approval, and target chain")
    p_audit_proposal.add_argument("proposal_id")
    p_audit_proposal.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit_proposal.add_argument("--output", default=None, help="Write full JSON audit report to this path")
    p_audit_proposal.add_argument("--rebuild-graph", action="store_true", help="Rebuild experience graph before audit")
    p_audit_proposal.add_argument("--redaction", choices=["standard", "strict", "none"], default="standard", help="Redaction profile for audit JSON")
    p_audit_proposal.set_defaults(func=cmd_audit_proposal)
    p_audit_bundle = audit_sub.add_parser("bundle", help="Export a project-level governance audit bundle")
    p_audit_bundle.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit_bundle.add_argument("--output", default=None, help="Write full JSON audit bundle to this path")
    p_audit_bundle.add_argument("--rebuild-graph", action="store_true", help="Rebuild experience graph before bundling")
    p_audit_bundle.add_argument("--limit-runs", type=int, default=20, help="Maximum recent runs to include")
    p_audit_bundle.add_argument("--redaction", choices=["standard", "strict", "none"], default="standard", help="Redaction profile for audit JSON")
    p_audit_bundle.set_defaults(func=cmd_audit_bundle)
    p_audit_check = audit_sub.add_parser("check", help="Run a CI-friendly governance audit gate")
    p_audit_check.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_audit_check.add_argument("--output", default=None, help="Write full JSON check report to this path")
    p_audit_check.add_argument("--rebuild-graph", action="store_true", help="Rebuild experience graph before checking")
    p_audit_check.add_argument("--limit-runs", type=int, default=20, help="Maximum recent runs to inspect")
    p_audit_check.add_argument("--max-pending", type=int, default=None, help="Fail if pending proposals exceed this count")
    p_audit_check.add_argument("--max-high-risk-pending", type=int, default=0, help="Fail if high-risk/p0 pending proposals exceed this count")
    p_audit_check.add_argument("--require-graph", action="store_true", help="Fail if the experience graph has no nodes")
    p_audit_check.add_argument("--fail-on-latest-failure", action="store_true", help="Fail if the latest included run failed or needs human review")
    p_audit_check.add_argument("--strict", action="store_true", help="Require zero pending proposals, a graph, and a clean latest run")
    p_audit_check.add_argument("--redaction", choices=["standard", "strict", "none"], default="standard", help="Redaction profile for audit JSON")
    p_audit_check.set_defaults(func=cmd_audit_check)

    p_workspace = sub.add_parser("workspace", help="Per-task isolated workspace commands")
    workspace_sub = p_workspace.add_subparsers(dest="workspace_command", required=True)
    p_workspace_list = workspace_sub.add_parser("list", help="List isolated workspaces")
    p_workspace_list.set_defaults(func=cmd_workspace_list)
    p_workspace_cleanup = workspace_sub.add_parser("cleanup", help="Remove completed/failed isolated workspaces")
    p_workspace_cleanup.add_argument("--all", action="store_true", help="Remove all recorded isolated workspaces")
    p_workspace_cleanup.add_argument("--status", default=None, help="Only remove workspaces with this status")
    p_workspace_cleanup.set_defaults(func=cmd_workspace_cleanup)

    p_spec = sub.add_parser("spec", help="Spec-aware experience commands")
    spec_sub = p_spec.add_subparsers(dest="spec_command", required=True)
    p_spec_check = spec_sub.add_parser("check", help="Check spec quality and extracted acceptance signals")
    p_spec_check.add_argument("--spec", default=None, help="Specific spec file to check")
    p_spec_check.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_spec_check.set_defaults(func=cmd_spec_check)
    p_spec_verify = spec_sub.add_parser("verify", help="Verify a run against attached or explicit spec files")
    p_spec_verify.add_argument("id", nargs="?", default="latest", help="Run/task ID, or latest")
    p_spec_verify.add_argument("--spec", action="append", default=[], help="Specific spec file to verify against")
    p_spec_verify.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_spec_verify.set_defaults(func=cmd_spec_verify)

    p_constitution = sub.add_parser("constitution", help="Experience constitution commands")
    constitution_sub = p_constitution.add_subparsers(dest="constitution_command", required=True)
    p_constitution_show = constitution_sub.add_parser("show", help="Show .praxile/constitution.md")
    p_constitution_show.set_defaults(func=cmd_constitution_show)
    p_constitution_check = constitution_sub.add_parser("check", help="Check that the experience constitution is initialized")
    p_constitution_check.set_defaults(func=cmd_constitution_check)

    p_feedback = sub.add_parser("feedback", help="Record user feedback for a run, proposal, or experience asset")
    p_feedback.add_argument("target", help="latest, <TASK_ID>, prop_<ID>, proposal:<ID>, or asset")
    p_feedback.add_argument("asset_path", nargs="?", help="Asset path when target is `asset`")
    feedback_sentiment = p_feedback.add_mutually_exclusive_group()
    feedback_sentiment.add_argument("--positive", nargs="?", const="", default=None, help="Positive feedback text")
    feedback_sentiment.add_argument("--negative", nargs="?", const="", default=None, help="Negative feedback text")
    feedback_sentiment.add_argument("--helpful", nargs="?", const="", default=None, help="Mark an asset/proposal helpful")
    feedback_sentiment.add_argument("--harmful", nargs="?", const="", default=None, help="Mark an asset/proposal harmful")
    p_feedback.add_argument("--text", default=None, help="Feedback text when not using --positive/--negative value")
    p_feedback.set_defaults(func=cmd_feedback)

    p_index = sub.add_parser("index", help="Experience index maintenance")
    index_sub = p_index.add_subparsers(dest="index_command", required=True)
    p_index_status = index_sub.add_parser("status", help="Show SQLite/FTS index health")
    p_index_status.add_argument("--scan", action="store_true", help="Run an explicit deep file scan for missing/stale assets")
    p_index_status.set_defaults(func=cmd_index_status)
    p_index_update = index_sub.add_parser("update", help="Process queued index changes or mark paths dirty")
    p_index_update.add_argument("--path", action="append", default=[], help="Project-relative asset path to mark dirty before update")
    p_index_update.add_argument("--limit", type=int, default=200, help="Maximum queued events to process")
    p_index_update.set_defaults(func=cmd_index_update)
    p_index_watch = index_sub.add_parser("watch", help="Explicit one-shot scan that queues changed assets, then updates")
    p_index_watch.add_argument("--once", action="store_true", help="Run one scan/update pass and exit")
    p_index_watch.add_argument("--limit", type=int, default=500, help="Maximum queued events to process")
    p_index_watch.set_defaults(func=cmd_index_watch)
    p_index_rebuild = index_sub.add_parser("rebuild", help="Rebuild the experience index from .praxile assets")
    p_index_rebuild.set_defaults(func=cmd_index_rebuild)

    p_consolidate = sub.add_parser("consolidate", help="Generate proposal-only experience consolidation suggestions")
    p_consolidate.add_argument("--duplicates", action="store_true", help="Inspect duplicate or overlapping assets")
    p_consolidate.add_argument("--stale", action="store_true", help="Inspect old unused assets")
    p_consolidate.add_argument("--conflicts", "--contradictions", dest="conflicts", action="store_true", help="Inspect assets with possible conflicting or contradictory guidance")
    p_consolidate.add_argument("--low-value", action="store_true", help="Inspect low-confidence or poor-outcome assets")
    p_consolidate.add_argument("--experience", action="store_true", help="Run all experience governance checks")
    p_consolidate.add_argument("--all", action="store_true", help="Run all consolidation checks")
    p_consolidate.add_argument("--summary", action="store_true", help="Print governance counts without creating a proposal")
    p_consolidate.add_argument("--stale-days", type=int, default=None, help="Age threshold for --stale")
    p_consolidate.set_defaults(func=cmd_consolidate)

    p_models = sub.add_parser("models", help="List model providers and routes")
    p_models.add_argument("--stats", action="store_true", help="Show model routing performance aggregated from trajectories")
    p_models.add_argument("--limit", type=int, default=200, help="Trajectory limit for --stats")
    p_models.set_defaults(func=cmd_models)

    p_tools = sub.add_parser("tools", help="List Praxile tool actions")
    p_tools.set_defaults(func=cmd_tools)

    p_mine_patterns = sub.add_parser("mine-patterns", help="Mine cross-task patterns from historical episodes")
    p_mine_patterns.set_defaults(func=cmd_mine_patterns)

    p_terminal = sub.add_parser("terminal", help="Start the interactive Praxile terminal")
    p_terminal.add_argument("--command", action="append", default=[], help="Run a terminal command non-interactively")
    p_terminal.set_defaults(func=cmd_terminal)

    p_channel = sub.add_parser("channel", help="Channel binding commands")
    channel_sub = p_channel.add_subparsers(dest="channel_command", required=True)
    p_channel_list = channel_sub.add_parser("list", help="List Telegram/Discord bindings")
    p_channel_list.set_defaults(func=cmd_channel_list)
    p_channel_show = channel_sub.add_parser("show", help="Show one channel binding")
    p_channel_show.add_argument("binding_id")
    p_channel_show.set_defaults(func=cmd_channel_show)
    p_channel_bind = channel_sub.add_parser("bind", help="Bind a Telegram chat or Discord channel")
    p_channel_bind.add_argument("platform", choices=["telegram", "discord"])
    p_channel_bind.add_argument("channel_id")
    p_channel_bind.add_argument("--guild-id", default=None, help="Discord guild/server ID")
    p_channel_bind.add_argument("--thread-id", default=None, help="Telegram topic ID or Discord thread ID")
    p_channel_bind.add_argument("--name", default=None, help="Human-readable binding name")
    p_channel_bind.add_argument("--kind", choices=["home", "project", "alert", "review"], default="home")
    p_channel_bind.add_argument("--mode", choices=["notify", "task", "bidirectional"], default="notify")
    p_channel_bind.add_argument("--token-env", default=None, help="Environment variable holding the bot token")
    p_channel_bind.add_argument("--free-response", action="store_true", help="Allow free-response mode in this channel")
    p_channel_bind.add_argument(
        "--require-mention",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require bot mention for channel-triggered tasks",
    )
    p_channel_bind.add_argument(
        "--auto-thread",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Discord: create/use threads for task runs",
    )
    p_channel_bind.add_argument("--skill", default=None, help="Default Praxile skill to load for this channel")
    p_channel_bind.add_argument("--prompt", default=None, help="Channel-specific instruction prompt")
    p_channel_bind.add_argument("--project-scope", default="current", help="Scope label for this binding")
    p_channel_bind.add_argument("--default", action="store_true", help="Make this the default channel binding")
    p_channel_bind.set_defaults(func=cmd_channel_bind)
    p_channel_unbind = channel_sub.add_parser("unbind", help="Remove a channel binding")
    p_channel_unbind.add_argument("binding_id")
    p_channel_unbind.set_defaults(func=cmd_channel_unbind)
    p_channel_env = channel_sub.add_parser("env", help="Print environment overlay names for bound channels")
    p_channel_env.set_defaults(func=cmd_channel_env)

    p_rollback = sub.add_parser("rollback", help="Rollback a task's edits or an accepted proposal")
    p_rollback.add_argument("id", help="Task ID or accepted proposal ID")
    p_rollback.set_defaults(func=cmd_rollback)

    p_memory = sub.add_parser("memory", help="Memory commands")
    memory_sub = p_memory.add_subparsers(dest="memory_command", required=True)
    p_memory_list = memory_sub.add_parser("list", help="List memory files")
    p_memory_list.add_argument("--include-inactive", action="store_true", help="Include deprecated/superseded/archived memory assets")
    p_memory_list.set_defaults(func=cmd_memory_list)
    p_memory_show = memory_sub.add_parser("show", help="Show a memory scope")
    p_memory_show.add_argument("scope", choices=["user", "project", "decisions", "failures"])
    p_memory_show.set_defaults(func=cmd_memory_show)
    p_memory_append = memory_sub.add_parser("append", help="Append a manual memory note")
    p_memory_append.add_argument("scope", choices=["user", "project", "decisions", "failures"])
    p_memory_append.add_argument("text")
    p_memory_append.set_defaults(func=cmd_memory_append)
    p_memory_search = memory_sub.add_parser("search", help="Search project memory")
    p_memory_search.add_argument("query")
    p_memory_search.set_defaults(func=cmd_memory_search)

    p_skill = sub.add_parser("skill", help="Skill commands")
    skill_sub = p_skill.add_subparsers(dest="skill_command", required=True)
    p_skill_list = skill_sub.add_parser("list", help="List skills")
    p_skill_list.set_defaults(func=cmd_skill_list)
    p_skill_show = skill_sub.add_parser("show", help="Show a project-local skill")
    p_skill_show.add_argument("name")
    p_skill_show.set_defaults(func=cmd_skill_show)
    p_skill_history = skill_sub.add_parser("history", help="Show accepted skill version snapshots")
    p_skill_history.add_argument("name")
    p_skill_history.set_defaults(func=cmd_skill_history)
    p_skill_search = skill_sub.add_parser("search", help="Search project-local skills")
    p_skill_search.add_argument("query")
    p_skill_search.set_defaults(func=cmd_skill_search)

    p_asset = sub.add_parser("asset", help="Experience asset lifecycle commands")
    asset_sub = p_asset.add_subparsers(dest="asset_command", required=True)
    p_asset_status = asset_sub.add_parser("status", help="Show indexed asset lifecycle metadata")
    p_asset_status.add_argument("path", help="Project-local asset path, with or without .praxile/")
    p_asset_status.set_defaults(func=cmd_asset_status)
    p_asset_deprecate = asset_sub.add_parser("deprecate", help="Mark an experience asset deprecated")
    p_asset_deprecate.add_argument("path")
    p_asset_deprecate.add_argument("--replaced-by", default=None)
    p_asset_deprecate.add_argument("--reason", default=None)
    p_asset_deprecate.set_defaults(func=cmd_asset_deprecate)
    p_asset_supersede = asset_sub.add_parser("supersede", help="Mark an experience asset superseded by another asset")
    p_asset_supersede.add_argument("path")
    p_asset_supersede.add_argument("--replaced-by", required=True)
    p_asset_supersede.add_argument("--reason", default=None)
    p_asset_supersede.set_defaults(func=cmd_asset_supersede)
    p_asset_archive = asset_sub.add_parser("archive", help="Mark an experience asset archived")
    p_asset_archive.add_argument("path")
    p_asset_archive.add_argument("--reason", default=None)
    p_asset_archive.set_defaults(func=cmd_asset_archive)
    p_asset_reactivate = asset_sub.add_parser("reactivate", help="Reactivate a deprecated, superseded, or archived asset")
    p_asset_reactivate.add_argument("path")
    p_asset_reactivate.add_argument("--reason", default=None)
    p_asset_reactivate.set_defaults(func=cmd_asset_reactivate)
    p_asset_diff = asset_sub.add_parser("diff", help="Compare two experience assets")
    p_asset_diff.add_argument("path")
    p_asset_diff.add_argument("--with", dest="with_asset", required=True, help="Replacement or comparison asset path")
    p_asset_diff.set_defaults(func=cmd_asset_diff)

    p_gateway = sub.add_parser("gateway", help="Gateway commands")
    gateway_sub = p_gateway.add_subparsers(dest="gateway_command", required=True)
    p_gateway_serve = gateway_sub.add_parser("serve", help="Serve the local HTTP gateway")
    p_gateway_serve.add_argument("--host", default="127.0.0.1")
    p_gateway_serve.add_argument("--port", type=int, default=8765)
    p_gateway_serve.add_argument("--token", default=None, help="Optional bearer token")
    p_gateway_serve.set_defaults(func=cmd_gateway_serve)

    p_doctor = sub.add_parser("doctor", help="Check harness configuration")
    p_doctor.add_argument("--online", action="store_true", help="Run fast model endpoint reachability checks")
    p_doctor.set_defaults(func=cmd_doctor)

    p_interop = sub.add_parser("interop", help="Explain optional framework adapter boundaries")
    p_interop.set_defaults(func=cmd_interop)
    return parser


def load(project_root: Path) -> tuple[Config, ExperienceStore]:
    config = Config.load(project_root)
    store = ExperienceStore(config.paths)
    return config, store


def cmd_init(args: argparse.Namespace, project_root: Path) -> int:
    config = Config.load(project_root)
    store = ExperienceStore(config.paths)
    config_exists = config.paths.config.exists()
    profile = None if args.no_detect else inspect_project(config.paths.root)
    if profile:
        config.data.setdefault("project", {})["detected_stacks"] = profile.stacks
        config.data.setdefault("project", {})["detected_markers"] = profile.markers
        config.data.setdefault("project", {})["detected_package_manager"] = profile.package_manager
        config.data.setdefault("project", {})["detected_test_commands"] = profile.test_commands
    seeded_commands = args.test_command or []
    if not seeded_commands and profile and (args.force or not config_exists):
        seeded_commands = profile.test_commands
    if seeded_commands:
        config.data.setdefault("runtime", {})["default_test_commands"] = seeded_commands
    store.initialize(config, force=args.force)
    if args.wizard:
        run_setup_wizard(args, config)
    if args.force or not config_exists or profile or args.test_command or args.wizard:
        config.write()
    print(f"Initialized {config.paths.state}")
    print(f"Config: {config.paths.config}")
    if profile:
        print(f"Detected stacks: {', '.join(profile.stacks) if profile.stacks else '(none)'}")
        print(f"Detected markers: {', '.join(profile.markers) if profile.markers else '(none)'}")
        if profile.package_manager:
            print(f"Package manager: {profile.package_manager}")
        commands = config.get("runtime", "default_test_commands", default=[])
        print(f"Default verification commands: {', '.join(commands) if commands else '(none)'}")
        if profile.missing_tools:
            print(f"Missing tools for detected commands: {', '.join(profile.missing_tools)}")
    commands = config.get("runtime", "default_test_commands", default=[])
    example_test = commands[0] if commands else "python -m pytest"
    print("Next steps:")
    if not has_configured_models(config):
        print("1. Configure a model provider: praxile setup")
        print("2. Verify model reachability: praxile doctor --online")
        print(f"3. Run your first task: praxile run \"Fix the failing test\" --test-command {shlex.quote(example_test)}")
    else:
        print(f"1. Run your first task: praxile run \"Fix the failing test\" --test-command {shlex.quote(example_test)}")
        print("2. Review what Praxile learned: praxile review --interactive")
        print("3. Explain the experience loop: praxile explain latest")
    print("Tip: config is project-local in .praxile/config.json; API keys stay in environment variables.")
    return 0


def cmd_setup(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    run_setup_wizard(args, config)
    config.write()
    print(f"Updated {config.paths.config}")
    if has_configured_models(config):
        print("Next: run `praxile doctor --online` to test model reachability.")
    else:
        print("No model provider configured. Praxile can still run demos, inspect state, and manage proposals.")
    if ChannelSystem(config).list_bindings():
        print("Channel bindings:")
        for binding in ChannelSystem(config).list_bindings():
            print(f"- {binding.id} platform={binding.platform} token_env={binding.token_env}")
    return 0


def cmd_spec_check(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = check_spec_file(config.paths.root, getattr(args, "spec", None))
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_spec_check(report))
    return 0


def cmd_spec_verify(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    trajectory = store.latest_trajectory() if args.id in {None, "latest"} else store.get_trajectory(args.id)
    if not trajectory:
        print("No trajectory found.")
        return 1
    report = verify_spec_compliance(config.paths.root, trajectory, explicit_specs=args.spec or None)
    trajectory["spec_compliance"] = report
    store.update_trajectory(trajectory)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_spec_compliance(report))
    return 0


def cmd_constitution_show(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    path = config.paths.state / "constitution.md"
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_constitution_check(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    path = config.paths.state / "constitution.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    required = [
        "No durable asset without evidence",
        "No global rule from a single run",
        "No memory update without scope and anti-scope",
        "No proposal accepted without source task and rollback path",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        print("Experience constitution: incomplete")
        for item in missing:
            print(f"- missing: {item}")
        return 1
    print("Experience constitution: ok")
    print(f"Path: {path}")
    return 0


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


def cmd_demo(args: argparse.Namespace, project_root: Path) -> int:
    demo_root = Path(args.path).resolve() if args.path else Path(tempfile.mkdtemp(prefix="praxile-demo-")).resolve()
    marker = demo_root / ".praxile-demo"
    if demo_root.exists() and any(demo_root.iterdir()) and not marker.exists() and not args.force:
        raise ValueError("demo --path must be empty, an existing Praxile demo directory, or use --force")
    demo_root.mkdir(parents=True, exist_ok=True)
    marker.write_text("This directory is owned by `praxile demo`.\n", encoding="utf-8")
    if args.fast:
        return run_fast_demo(args, demo_root)

    print("[1/6] Creating demo project")
    calculator = demo_root / "calculator.py"
    test_file = demo_root / "test_calculator.py"
    calculator.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    test_file.write_text(
        "import unittest\n\n"
        "from calculator import add\n\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )

    config = Config.load(demo_root)
    config.data.setdefault("runtime", {})["default_test_commands"] = ["python -m unittest"]
    store = ExperienceStore(config.paths)
    store.initialize(config)
    config.write()

    safety = SafetyPolicy(config)
    fs = FileSystemEnv(config, safety)
    git = GitEnv(config)
    shell = ShellEnv(config, safety)
    tests = TestEnv(config, shell)
    project = ProjectEnv(config, fs, git, tests)
    task = "Demo: fix failing calculator unittest and learn from the repair"
    logger = TrajectoryLogger(task, project.snapshot(refresh=True))
    retrieved = store.retrieve("calculator unittest repair", limit=8)
    logger.set_loaded_context(retrieved)
    store.record_asset_usage(logger.task_id, retrieved, used_in_prompt=True)
    logger.set_task_analysis(
        {
            "task_type": "bugfix",
            "risk_level": "low",
            "architecture_gate_required": False,
            "ui_human_review_required": False,
            "privacy_sensitive": False,
            "high_risk": False,
            "confidence": 1.0,
            "reasons": ["Deterministic local demo task."],
            "signals": {"demo": True},
            "frozen_hits": [],
        }
    )
    logger.set_plan(
        [
            "Run the failing unittest to capture objective evidence.",
            "Read the buggy calculator implementation.",
            "Apply the smallest scoped fix.",
            "Run unittest again and generate reward/proposals.",
        ]
    )

    print("[2/6] Running failing test")
    before_results = tests.run(["python -m unittest"])
    for result in before_results:
        logger.add_action(
            action_type="run_test",
            input_data={"command": result.get("data", {}).get("command")},
            observation=result,
            status=result.get("status", "unknown"),
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
        "def add(left, right):\n    return left + right  # fixed by Praxile demo\n",
        task_id=logger.task_id,
        step=len(logger.data["actions"]) + 1,
    ).to_dict()
    logger.add_action(
        action_type="edit_file",
        input_data={"path": "calculator.py"},
        observation=edit_observation,
        status=edit_observation.get("status", "unknown"),
    )
    shutil.rmtree(demo_root / "__pycache__", ignore_errors=True)
    print("[4/6] Running verification")
    after_results = tests.run(["python -m unittest"])
    for result in after_results:
        logger.add_action(
            action_type="run_test",
            input_data={"command": result.get("data", {}).get("command")},
            observation=result,
            status=result.get("status", "unknown"),
        )
    logger.set_diff_summary(git.diff_summary())
    trajectory = logger.finish(status="completed", summary="Fixed the demo calculator bug and captured learning signals.")
    print("[5/6] Generating reward and proposals")
    report = RewardEngine(config).build_report(trajectory, after_results)
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
            accepted = store.apply_proposal(memory)
            accepted_id = accepted["proposal_id"]
            retrieved_after_accept = store.retrieve("calculator unittest repair", kinds=["memory"], limit=5)

    print("[6/6] Showing next-run retrieval evidence")
    print(f"Praxile demo project: {demo_root}")
    print(f"Task: {trajectory['task_id']}")
    print(f"Before unittest: {before_results[0].get('status') if before_results else 'unknown'}")
    print(f"After unittest: {after_results[0].get('status') if after_results else 'unknown'}")
    print(f"Reward overall: {report.get('overall')}")
    print_run_evolution_summary(trajectory)
    if accepted_id:
        print(f"Accepted demo memory proposal: {accepted_id}")
        print(f"Retrieval after accept: {len(retrieved_after_accept)} memory match(es)")
    else:
        print("No proposal was auto-accepted. Run:")
        print(f"  praxile --project {demo_root} review --interactive")
    print(f"Explain with: praxile --project {demo_root} explain {trajectory['task_id']}")
    print("Next steps:")
    print(f"1. praxile --project {demo_root} explain latest")
    print(f"2. praxile --project {demo_root} review --interactive")
    print('3. Run `praxile run "your task" --test-command "python -m pytest"` in your own repo.')
    if getattr(args, "show_files", False):
        print_demo_files(demo_root)
    return 0


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
            accepted = store.apply_proposal(memory)
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


def cmd_run(args: argparse.Namespace, project_root: Path) -> int:
    if not args.task and not args.resume:
        raise ValueError("run requires a task, or --resume <TASK_ID>")
    config, store = load(project_root)
    route_overrides = apply_run_overrides(args, config)
    store.initialize(config)
    workspace_mode = getattr(args, "workspace_mode", None) or str(config.get("workspace", "default_mode", default="in-place"))
    if workspace_mode != "in-place":
        return run_in_isolated_workspace(args, config, store, route_overrides, workspace_mode)
    runtime = AgentRuntime(config)
    trajectory = runtime.run(
        args.task or "",
        test_commands=args.test_command or None,
        max_steps=args.max_steps,
        dry_run=args.dry_run,
        resume=args.resume,
        spec_files=args.spec or None,
        parallel_readonly_explore=getattr(args, "parallel_readonly_explore", None),
    )
    print(f"Task: {trajectory['task_id']}")
    print(f"Status: {trajectory['result']['status']}")
    if route_overrides:
        print("Route overrides:")
        for key, value in route_overrides.items():
            print(f"- {key}: {value}")
    if trajectory.get("dry_run"):
        print("Mode: dry-run (edits and shell commands were blocked)")
    spec_context = trajectory.get("spec_context") or {}
    if spec_context.get("enabled"):
        print(
            "Spec context: "
            f"{spec_context.get('quality_label')} score={spec_context.get('quality_score')} "
            f"files={len(spec_context.get('spec_files') or [])}"
        )
    print(f"Summary: {trajectory['result']['summary']}")
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
            gate = proposal.get("proposal_gate") or {}
            gate_text = f" gate={gate.get('decision')}" if gate else ""
            print(
                f"- {proposal['proposal_id']} [{proposal['type']}] "
                f"risk={proposal.get('risk_level')} confidence={proposal.get('confidence_level', proposal.get('confidence'))} "
                f"{proposal['title']}{gate_text}"
            )
    print_run_evolution_summary(trajectory)
    print(f"Review with: praxile review --source-run {trajectory['task_id']}")
    print(f"Explain with: praxile explain {trajectory['task_id']}")
    return 0


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
    trajectory: dict[str, Any] | None = None
    try:
        trajectory = AgentRuntime(workspace_config).run(
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


def cmd_review(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    proposal_filters_requested = any(
        bool(getattr(args, name, None))
        for name in ["proposal_type", "risk", "confidence", "source_run", "older_than", "summary", "high_risk", "recommended"]
    )
    if args.interactive:
        pending = filter_and_sort_proposals(store.list_proposals(status="pending"), args)
        pending = filter_by_recommended_action(store, pending, getattr(args, "recommended", None))
        return review_pending_interactively(store, proposals=pending)
    if args.pending or proposal_filters_requested:
        pending = filter_and_sort_proposals(store.list_proposals(status="pending"), args)
        pending = filter_by_recommended_action(store, pending, getattr(args, "recommended", None))
        if not pending:
            print("No pending proposals.")
            return 0
        if getattr(args, "summary", False):
            print_proposal_inbox_summary(pending)
            return 0
        print_pending_proposals(pending)
        print("\nUse `praxile review <proposal_id>` or `praxile review --interactive`.")
        return 0
    item_id = args.id
    proposal = store.find_proposal(item_id) if item_id else None
    if proposal:
        print_proposal(proposal, use_pager=args.pager)
        return 0
    trajectory = store.get_trajectory(item_id) if item_id else store.latest_trajectory()
    if not trajectory:
        print("No trajectory or proposal found.")
        return 1
    print_trajectory(trajectory, use_pager=args.pager)
    pending = [
        store.find_proposal(candidate.get("proposal_id"), status="pending")
        for candidate in trajectory.get("experience_candidates", [])
    ]
    pending = [item for item in pending if item]
    if pending:
        print("\nPending proposals:")
        for proposal in pending:
            print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
        print("\nUse `praxile review <proposal_id>` to inspect a proposal diff, or `praxile review --interactive`.")
    return 0


def cmd_accept(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if getattr(args, "all_low_risk", False):
        pending = filter_and_sort_proposals(store.list_proposals(status="pending"), argparse.Namespace(risk="low"))
        excluded_types = {"architecture_gate", "frozen_boundary", "harness_rule", "routing"}
        skipped = [
            proposal
            for proposal in pending
            if proposal.get("risk_level", "low") != "low" or proposal.get("type") in excluded_types
        ]
        proposals = [
            proposal
            for proposal in pending
            if proposal.get("risk_level", "low") == "low" and proposal.get("type") not in excluded_types
        ]
        if args.limit is not None:
            proposals = proposals[: max(0, int(args.limit))]
        if not proposals and not skipped:
            print("No low-risk pending proposals.")
            return 0
        if getattr(args, "dry_run", False) or not getattr(args, "yes", False):
            print("Dry run: no proposals accepted.")
            if proposals:
                print("Will accept with --yes:")
                for proposal in proposals:
                    print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
            else:
                print("Will accept with --yes: none")
            if skipped:
                print("Will skip:")
                for proposal in skipped:
                    print(
                        f"- {proposal['proposal_id']} [{proposal['type']}] "
                        f"risk={proposal.get('risk_level')} {proposal['title']}"
                    )
            print("Run `praxile accept --all-low-risk --yes` to apply this batch.")
            return 0
        accepted_count = 0
        for proposal in proposals:
            accepted = store.apply_proposal(proposal)
            accepted_count += 1
            print(f"Accepted {accepted['proposal_id']}: {accepted['title']}")
        if skipped:
            print("Skipped proposals that require individual review:")
            for proposal in skipped:
                print(f"- {proposal['proposal_id']} [{proposal['type']}] risk={proposal.get('risk_level')}")
        print(f"Accepted {accepted_count} low-risk proposal(s). High-risk proposals are never accepted in batch.")
        return 0
    if not args.proposal_id:
        raise ValueError("accept requires <PROPOSAL_ID>, or use --all-low-risk")
    proposal = store.find_proposal(args.proposal_id, status="pending")
    if not proposal:
        print("No pending proposal found.")
        return 1
    accepted = store.apply_proposal(proposal)
    print(f"Accepted {accepted['proposal_id']}: {accepted['title']}")
    for change in accepted.get("applied_changes", []):
        print(f"- {change['path']}")
    return 0


def cmd_reject(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    batch_requested = bool(getattr(args, "low_confidence", False) or getattr(args, "older_than", None))
    if batch_requested and not args.proposal_id:
        pending = store.list_proposals(status="pending")
        if getattr(args, "low_confidence", False):
            pending = [proposal for proposal in pending if proposal_confidence_level(proposal) == "low"]
        if getattr(args, "older_than", None):
            pending = [proposal for proposal in pending if proposal_older_than(proposal, args.older_than)]
        pending = filter_and_sort_proposals(pending, argparse.Namespace())
        if not pending:
            print("No matching pending proposals.")
            return 0
        reason = args.reason or "batch rejection"
        for proposal in pending:
            rejected = store.reject_proposal(proposal, reason=reason)
            print(f"Rejected {rejected['proposal_id']}: {rejected['title']}")
        print(f"Rejected {len(pending)} proposal(s).")
        return 0
    if not args.proposal_id:
        raise ValueError("reject requires <PROPOSAL_ID>, or a batch flag such as --low-confidence")
    proposal = store.find_proposal(args.proposal_id, status="pending")
    if not proposal:
        print("No pending proposal found.")
        return 1
    rejected = store.reject_proposal(proposal, reason=args.reason)
    print(f"Rejected {rejected['proposal_id']}: {rejected['title']}")
    return 0


def cmd_explain(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    trajectory = store.latest_trajectory() if args.id in {None, "latest"} else store.get_trajectory(args.id)
    if not trajectory:
        print("No trajectory found.")
        return 1
    explanation = build_run_explanation(store, trajectory)
    if getattr(args, "json", False):
        print(json.dumps(explanation, indent=2, ensure_ascii=False))
        return 0
    print_run_explanation(explanation)
    return 0


def cmd_graph_status(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    rebuild_result = store.rebuild_experience_graph() if getattr(args, "rebuild", False) else None
    status = store.graph_status()
    if rebuild_result:
        status["last_rebuild"] = rebuild_result
    if getattr(args, "json", False):
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print_graph_status(status)
    return 0


def cmd_graph_rebuild(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    result = store.rebuild_experience_graph()
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Rebuilt Praxile experience graph.")
        print(f"- nodes: {result.get('nodes')}")
        print(f"- edges: {result.get('edges')}")
        print_relation_counts(result.get("relation_counts") or {})
    return 0


def cmd_graph_explain(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if getattr(args, "rebuild", False):
        store.rebuild_experience_graph()
    report = store.graph_explain(args.ref, depth=args.depth, limit=args.limit)
    if not report.get("found") and not getattr(args, "rebuild", False):
        store.rebuild_experience_graph()
        report = store.graph_explain(args.ref, depth=args.depth, limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report.get("found") else 1
    print_graph_report(report, title=f"Experience graph: {args.ref}")
    return 0 if report.get("found") else 1


def cmd_graph_trace(args: argparse.Namespace, project_root: Path) -> int:
    args.ref = args.proposal_id if str(args.proposal_id).startswith("proposal:") else f"proposal:{args.proposal_id}"
    return cmd_graph_explain(args, project_root)


def cmd_graph_impact(args: argparse.Namespace, project_root: Path) -> int:
    args.ref = args.spec_id if str(args.spec_id).startswith("spec:") else f"spec:{args.spec_id}"
    return cmd_graph_explain(args, project_root)


def cmd_audit_run(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = build_run_audit(config, store, args.id, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)


def cmd_audit_asset(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = build_asset_audit(config, store, args.path, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)


def cmd_audit_proposal(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = build_proposal_audit(config, store, args.proposal_id, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)


def cmd_audit_bundle(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = build_project_audit_bundle(
        config,
        store,
        limit_runs=args.limit_runs,
        rebuild_graph=args.rebuild_graph,
        redaction=args.redaction,
    )
    return emit_audit_report(report, args)


def cmd_audit_check(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = build_project_audit_check(
        config,
        store,
        limit_runs=args.limit_runs,
        rebuild_graph=args.rebuild_graph,
        max_pending=args.max_pending,
        max_high_risk_pending=args.max_high_risk_pending,
        require_graph=args.require_graph,
        fail_on_latest_failure=args.fail_on_latest_failure,
        strict=args.strict,
        redaction=args.redaction,
    )
    return emit_audit_report(report, args)


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


def cmd_workspace_list(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    records = WorkspaceManager(config).list()
    if not records:
        print("No isolated workspaces.")
        return 0
    for record in records:
        print(
            f"{record.workspace_id}  mode={record.mode} status={record.status} "
            f"task={record.task_id or '-'} created={record.created_at} root={record.root}"
        )
    return 0


def cmd_workspace_cleanup(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    result = WorkspaceManager(config).cleanup(all_workspaces=bool(getattr(args, "all", False)), status=getattr(args, "status", None))
    print(f"Removed {len(result['removed'])} workspace(s).")
    for workspace_id in result["removed"]:
        print(f"- {workspace_id}")
    if result["skipped"]:
        print(f"Skipped {len(result['skipped'])} workspace(s).")
    return 0


def cmd_feedback(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if str(args.target or "").strip() == "auto":
        raw = str(args.text or getattr(args, "positive", None) or getattr(args, "negative", None) or "")
        classifier = FeedbackSemanticClassifier(config, ModelRouter(config))
        semantic = classifier.classify(raw, feedback_semantic_context(store))
        intents = semantic.get("feedback_events") or extract_feedback_intents(raw)
        if not intents:
            raise ValueError("feedback auto requires natural-language text")
        generated: list[dict] = []
        recorded = []
        for intent in intents:
            target_type, target_id = resolve_feedback_intent_target(store, intent)
            feedback = build_feedback(
                target_type=target_type,
                target_id=target_id,
                raw_text=str(intent.get("raw_text") or raw),
                sentiment=str(intent.get("sentiment") or "neutral"),
                feedback_type=str(intent.get("feedback_type") or "") or None,
                strength=float(intent.get("strength") or 0.0),
                effect=intent.get("effect") if isinstance(intent.get("effect"), dict) else None,
            )
            if intent.get("requires_confirmation"):
                feedback.setdefault("effect", {})["requires_confirmation"] = True
            if intent.get("semantic_reason"):
                feedback.setdefault("effect", {})["semantic_reason"] = intent.get("semantic_reason")
            if semantic.get("active"):
                feedback["semantic_classifier"] = {
                    "active": True,
                    "model_role": semantic.get("model_role"),
                    "provider": semantic.get("provider"),
                    "model": semantic.get("model"),
                    "route": semantic.get("route", {}),
                }
            path = store.record_feedback(feedback)
            recorded.append((feedback, path))
            if target_type == "run":
                update_run_reward_after_feedback(config, store, target_id)
            elif target_type == "asset" and feedback["sentiment"] == "negative":
                generated.append(write_asset_feedback_proposal(store, feedback))
        for feedback, path in recorded:
            print(f"Recorded feedback {feedback['feedback_id']} -> {path.relative_to(config.paths.root)}")
            print(
                f"Target: {feedback['target_type']} {feedback['target_id']} "
                f"sentiment={feedback['sentiment']} strength={feedback['strength']}"
            )
        if generated:
            print("Generated governed proposal(s) for durable asset changes:")
            for proposal in generated:
                print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
        return 0
    target_type, target_id = resolve_feedback_target(store, args)
    sentiment, raw_text = resolve_feedback_sentiment(args)
    feedback = build_feedback(
        target_type=target_type,
        target_id=target_id,
        raw_text=raw_text,
        sentiment=sentiment,
    )
    path = store.record_feedback(feedback)
    generated: list[dict] = []
    if target_type == "run":
        update_run_reward_after_feedback(config, store, target_id)
    elif target_type == "asset" and feedback["sentiment"] == "negative":
        generated.append(write_asset_feedback_proposal(store, feedback))
    print(f"Recorded feedback {feedback['feedback_id']} -> {path.relative_to(config.paths.root)}")
    print(
        f"Target: {feedback['target_type']} {feedback['target_id']} "
        f"sentiment={feedback['sentiment']} strength={feedback['strength']}"
    )
    if target_type == "run":
        reward = store.feedback_reward_for("run", target_id)
        print(f"Run user_feedback_reward: score={reward.get('score')} events={len(reward.get('events') or [])}")
    if generated:
        print("Generated governed proposal(s) for durable asset changes:")
        for proposal in generated:
            print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
    return 0


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


def cmd_history(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    rows = store.list_history(
        limit=args.limit,
        status=args.status,
        query=args.query,
        offset=max(0, int(args.offset or 0)),
    )
    if not rows:
        print("No task history yet.")
        return 0
    for row in rows:
        print(
            f"{row['task_id']}  {row['status']}  reward={row['reward_score']}  "
            f"{row['created_at']}  {row['user_task']}"
        )
    return 0


def cmd_index_status(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    print_index_status(store.index_status(scan=bool(getattr(args, "scan", False))))
    return 0


def cmd_index_update(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    for raw_path in getattr(args, "path", []) or []:
        store.mark_asset_dirty(config.paths.root / raw_path, event="cli_update")
    result = store.index_changed(limit=args.limit)
    print(f"Processed {result['processed']} index event(s): indexed={result['indexed']} removed={result['removed']}")
    return 0


def cmd_index_watch(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if not getattr(args, "once", False):
        print("Current MVP supports explicit one-shot watch only. Use: praxile index watch --once")
        return 2
    scan = store.queue_changed_assets_from_scan()
    result = store.index_changed(limit=args.limit)
    print(f"Scanned {scan['scanned']} asset stat(s), queued={scan['queued']}")
    print(f"Processed {result['processed']} index event(s): indexed={result['indexed']} removed={result['removed']}")
    return 0


def cmd_index_rebuild(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    store.reindex_all()
    print("Rebuilt Praxile experience index.")
    print_index_status(store.index_status(scan=True))
    return 0


def cmd_consolidate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    include_all = bool(getattr(args, "all", False) or getattr(args, "experience", False))
    explicit = any(
        bool(getattr(args, name, False))
        for name in ["duplicates", "stale", "conflicts", "low_value", "experience"]
    )
    checks = {
        "duplicates": include_all or getattr(args, "duplicates", False) or not explicit,
        "stale": include_all or getattr(args, "stale", False),
        "conflicts": include_all or getattr(args, "conflicts", False),
        "low_value": include_all or getattr(args, "low_value", False),
    }
    engine = ConsolidationEngine(config, store)
    if getattr(args, "summary", False):
        summary = engine.summary(**checks, stale_days=args.stale_days)
        print("Experience governance summary:")
        for key, value in summary.items():
            print(f"- {key}: {value}")
        return 0
    proposals = engine.generate(**checks, stale_days=args.stale_days)
    if not proposals:
        print("No consolidation proposals generated.")
        return 0
    for proposal in proposals:
        store.write_proposal(proposal)
        print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
    print("Review with: praxile review --pending")
    return 0


def cmd_models(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    router = ModelRouter(config)
    if getattr(args, "stats", False):
        stats = store.model_routing_stats(limit=args.limit)
        if not stats:
            print("No model routing stats yet.")
            return 0
        print("Model routing stats:")
        for item in stats:
            failures = item.get("failure_patterns") or {}
            failure_text = ", ".join(f"{name}={count}" for name, count in failures.items()) or "none"
            print(
                f"- task_type={item['task_type']} target={item['target']} runs={item['runs']} "
                f"completed={item['completed']} needs_human={item['needs_human']} failed={item['failed']} "
                f"avg_reward={item['average_reward']} avg_latency_ms={item['average_latency_ms']} "
                f"route_failures={item['performance_failures']} patterns={failure_text}"
            )
        return 0
    print("Model roles:")
    if not has_configured_models(config):
        print("- not configured: run `praxile setup` to choose a provider")
    for role_name, role in config.get("model_roles", default={}).items():
        if not isinstance(role, dict):
            continue
        target = f"{role.get('provider')}:{role.get('model')}"
        fallbacks = []
        for item in role.get("fallback", []) or []:
            if isinstance(item, dict):
                fallbacks.append(f"{item.get('provider')}:{item.get('model')}")
            elif isinstance(item, str):
                fallbacks.append(item)
        print(f"- {role_name}: {target}" + (f" fallback={', '.join(fallbacks)}" if fallbacks else ""))
    print("Routes:")
    for key, value in config.get("routing", default={}).items():
        if isinstance(value, str):
            provider_name = value.split(":", 1)[0] if ":" in value else value
            print(f"- {key}: {value} (provider_known={provider_name in router.providers})")
    print("Providers:")
    for name, provider in router.providers.items():
        print(f"- {name}")
        for model in provider.list_models():
            print(
                f"  - {model.name} role={model.role} "
                f"context={model.context_window} tools={model.supports_tools}"
            )
    return 0


from .patterns import PatternMiner
from .hypothesis import HypothesisGenerator, CounterexampleChecker
from .proposals import ProposalComposer

def cmd_mine_patterns(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    engine = EvolutionEngine(config)
    
    print("Mining patterns from historical episodes...")
    episodes = PatternMiner.load_all_episodes(config.paths.state)
    print(f"Loaded {len(episodes)} episodes.")
    
    router = ModelRouter(config)
    patterns = PatternMiner.update_index(config.paths.state, config=config, router=router)
    print(f"Mined {len(patterns)} candidate patterns.")
    for pattern in patterns[:10]:
        reasons = ", ".join(str(item) for item in pattern.get("match_reasons", [])[:4]) or "single-episode or weak similarity"
        print(
            f"- {pattern.get('pattern_id')} score={pattern.get('pattern_score')} "
            f"confidence={pattern.get('confidence')} evidence={len(pattern.get('episodes') or [])} "
            f"feedback_delta={pattern.get('confidence_adjustment_from_feedback', 0)} reasons={reasons}"
        )
        for reason in (pattern.get("semantic_reasons") or [])[:2]:
            print(f"  semantic: {reason}")
    
    hypotheses = HypothesisGenerator.generate(patterns)
    context = PatternMiner.load_feedback_context(config.paths.state)
    from .semantic_judges import CounterexampleSemanticChecker

    semantic_checker = CounterexampleSemanticChecker(config, router)
    validated = CounterexampleChecker.validate(hypotheses, episodes, context, semantic_checker=semantic_checker)
    
    proposals = ProposalComposer.compose(validated, engine)
    
    if not proposals:
        print("No high-confidence project patterns generated.")
        return 0
        
    for proposal in proposals:
        store.write_proposal(proposal)
        print(f"Generated proposal {proposal['proposal_id']} [{proposal['type']}]: {proposal['title']}")
        
    print("Review with: praxile review --pending")
    return 0


def cmd_tools(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    registry = ToolRegistry(config)
    for tool in registry.describe():
        print(f"- {tool['name']}: {tool['description']}")
    return 0


def cmd_terminal(args: argparse.Namespace, project_root: Path) -> int:
    session = TerminalSession(project_root)
    if args.command:
        keep_going = True
        for command in args.command:
            keep_going, output = session.handle(command)
            if output:
                print(output)
            if not keep_going:
                break
        return 0

    print(session.banner())
    while True:
        try:
            line = safe_input("praxile> ")
        except EOFError:
            print()
            return 0
        keep_going, output = session.handle(line)
        if output:
            print(output)
        if not keep_going:
            return 0


def cmd_channel_list(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    system = ChannelSystem(config)
    bindings = system.list_bindings()
    if not bindings:
        print("No channel bindings yet.")
        return 0
    default_id = config.get("channels", "default")
    for binding in bindings:
        marker = " default" if binding.id == default_id else ""
        print(
            f"{binding.id}{marker}  platform={binding.platform} mode={binding.mode} "
            f"kind={binding.kind} enabled={binding.enabled} token_env={binding.token_env}"
        )
    return 0


def cmd_channel_show(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).get(args.binding_id)
    if not binding:
        print("No channel binding found.")
        return 1
    print(json.dumps(binding.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_channel_bind(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).bind(
        args.platform,
        args.channel_id,
        guild_id=args.guild_id,
        thread_id=args.thread_id,
        name=args.name,
        kind=args.kind,
        mode=args.mode,
        token_env=args.token_env,
        require_mention=args.require_mention,
        allow_free_response=args.free_response,
        auto_thread=args.auto_thread,
        skill=args.skill,
        prompt=args.prompt,
        project_scope=args.project_scope,
        make_default=args.default,
    )
    print(f"Bound {binding.id}")
    print(f"Config: {config.paths.config}")
    print(f"Token source: ${binding.token_env}")
    return 0


def cmd_channel_unbind(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    binding = ChannelSystem(config).unbind(args.binding_id)
    print(f"Unbound {binding.id}")
    return 0


def cmd_channel_env(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    overlay = ChannelSystem(config).as_env_overlay()
    if not overlay:
        print("No enabled channel overlay values.")
        return 0
    for key in sorted(overlay):
        print(f"{key}={overlay[key]}")
    return 0


def cmd_rollback(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    proposal = store.find_proposal(args.id, status="accepted")
    if proposal:
        rolled = store.rollback_proposal(args.id)
        print(f"Rolled back proposal {rolled['proposal_id']}")
        return 0
    trajectory = store.get_trajectory(args.id)
    if not trajectory:
        print("No accepted proposal or task trajectory found.")
        return 1
    safety = SafetyPolicy(config)
    fs = FileSystemEnv(config, safety)
    git = GitEnv(config)
    shell = ShellEnv(config, safety)
    tests = TestEnv(config, shell)
    project = ProjectEnv(config, fs, git, tests)
    restored = project.rollback_task(trajectory)
    append_jsonl(
        config.paths.logs / "rollback.jsonl",
        {"event": "task_rollback", "task_id": trajectory["task_id"], "restored": restored, "created_at": utc_now()},
    )
    print(f"Rolled back task {trajectory['task_id']}")
    if restored:
        for item in restored:
            print(f"- {item['path']} ({item['mode']})")
    else:
        print("No edit backups were found for this task.")
    return 0


def cmd_memory_list(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    if getattr(args, "include_inactive", False):
        store.initialize(config)
        rows = store.list_assets("memory", include_inactive=True)
        if not rows:
            print("No memory assets.")
            return 0
        for row in rows:
            replaced = f" replaced_by={row.get('replaced_by')}" if row.get("replaced_by") else ""
            print(f"{row['path']} status={row.get('status', 'active')} usage={row.get('usage_count', 0)}{replaced}")
        return 0
    memory = MemorySystem(config)
    for entry in memory.list():
        print(f"{entry.scope}: {entry.path}")
    return 0


def cmd_memory_show(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    entry = MemorySystem(config).read(args.scope)
    print(f"# {entry.scope}: {entry.path}\n")
    print(entry.content)
    return 0


def cmd_memory_append(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    entry = MemorySystem(config).append(args.scope, args.text, source="praxile memory append")
    print(f"Updated {entry.path}")
    return 0


def cmd_memory_search(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    results = MemorySystem(config).search(args.query, limit=10)
    if not results:
        print("No memory matches.")
        return 0
    for item in results:
        print(f"- {item['path']} score={item['score']}")
        print(indent_block(shorten(item["snippet"], 500), "  "))
    return 0


def cmd_skill_list(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    skills = SkillSystem(config).list()
    if not skills:
        print("No accepted skills yet.")
        return 0
    for skill in skills:
        print(f"{skill.name}: {skill.path} version={skill.version} status={skill.status}")
    return 0


def cmd_skill_show(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    skill = SkillSystem(config).load(args.name)
    print(f"# {skill.name}: {skill.path} version={skill.version} status={skill.status}\n")
    print(skill.content)
    return 0


def cmd_skill_history(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    system = SkillSystem(config)
    metadata = system.metadata(args.name)
    history = system.history(args.name)
    print(f"{args.name}: status={metadata.get('status')} version={metadata.get('version')}")
    if not history:
        print("No version snapshots.")
        return 0
    for item in history:
        print(f"- {item['version']}: {item['path']}")
    return 0


def cmd_skill_search(args: argparse.Namespace, project_root: Path) -> int:
    config, _store = load(project_root)
    results = SkillSystem(config).search(args.query, limit=10)
    if not results:
        print("No skill matches.")
        return 0
    for item in results:
        print(f"- {item['path']} score={item['score']}")
        print(indent_block(shorten(item["snippet"], 500), "  "))
    return 0


def cmd_asset_status(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    path = normalize_asset_path(args.path)
    asset = store.get_asset(path)
    if not asset:
        print("No indexed asset found. Run `praxile index watch --once` if the file was edited manually.")
        return 1
    print(f"Asset: {asset['path']}")
    print(f"Type: {asset.get('type')}")
    print(f"Status: {asset.get('status', 'active')}")
    print(f"Retrieval: {'included' if asset.get('status', 'active') == 'active' else 'excluded from normal retrieval'}")
    print(f"Usage: {asset.get('usage_count', 0)} positive={asset.get('positive_outcome_count', 0)} negative={asset.get('negative_outcome_count', 0)}")
    for key in [
        "replaced_by",
        "deprecated_reason",
        "superseded_reason",
        "archived_reason",
        "reactivated_reason",
        "reactivated_at",
        "source_proposal",
        "updated_at",
    ]:
        if asset.get(key):
            print(f"{key}: {asset[key]}")
    events = asset.get("lifecycle_events") or []
    if isinstance(events, list) and events:
        print("Lifecycle history:")
        for event in events[-8:]:
            if not isinstance(event, dict):
                continue
            line = f"- {event.get('at', '(unknown)')}: {event.get('status', 'unknown')}"
            if event.get("reason"):
                line += f" reason={event.get('reason')}"
            if event.get("replaced_by"):
                line += f" replaced_by={event.get('replaced_by')}"
            if event.get("source"):
                line += f" source={event.get('source')}"
            print(line)
    if asset.get("replaced_by"):
        print(f"Compare: praxile asset diff {asset['path']} --with {asset['replaced_by']}")
    attributions = store.attribution_history_for_asset(path, limit=5)
    if attributions:
        print("Semantic attribution history:")
        for item in attributions:
            semantic = item.get("semantic_attribution") or {}
            print(
                f"- {item.get('updated_at')}: task={item.get('task_id')} outcome={item.get('outcome')} "
                f"level={semantic.get('attribution_level')} confidence={semantic.get('confidence')}"
            )
            if semantic.get("reason"):
                print(f"  reason={semantic.get('reason')}")
    return 0


def cmd_asset_deprecate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    asset = store.update_asset_status(
        normalize_asset_path(args.path),
        status="deprecated",
        replaced_by=args.replaced_by,
        reason=args.reason or "manual deprecation",
    )
    print(f"Deprecated {asset['path']}")
    if asset.get("replaced_by"):
        print(f"Replaced by: {asset['replaced_by']}")
    return 0


def cmd_asset_supersede(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    asset = store.update_asset_status(
        normalize_asset_path(args.path),
        status="superseded",
        replaced_by=normalize_asset_path(args.replaced_by),
        reason=args.reason or "manual supersede",
    )
    print(f"Superseded {asset['path']}")
    print(f"Replaced by: {asset.get('replaced_by')}")
    return 0


def cmd_asset_archive(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    asset = store.update_asset_status(
        normalize_asset_path(args.path),
        status="archived",
        reason=args.reason or "manual archive",
    )
    print(f"Archived {asset['path']}")
    return 0


def cmd_asset_reactivate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    asset = store.update_asset_status(
        normalize_asset_path(args.path),
        status="active",
        reason=args.reason or "manual reactivation",
    )
    print(f"Reactivated {asset['path']}")
    return 0


def cmd_asset_diff(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    left_path = normalize_asset_path(args.path)
    right_path = normalize_asset_path(args.with_asset)
    left = store.get_asset(left_path)
    right = store.get_asset(right_path)
    left_file = config.paths.root / left_path
    right_file = config.paths.root / right_path
    if not left_file.exists():
        print(f"Asset file not found: {left_path}")
        return 1
    if not right_file.exists():
        print(f"Asset file not found: {right_path}")
        return 1
    before = left_file.read_text(encoding="utf-8", errors="replace")
    after = right_file.read_text(encoding="utf-8", errors="replace")
    print(f"Left: {left_path} status={(left or {}).get('status', 'unknown')}")
    print(f"Right: {right_path} status={(right or {}).get('status', 'unknown')}")
    if left and left.get("replaced_by"):
        print(f"Left replaced_by: {left.get('replaced_by')}")
    print("")
    diff = unified_diff(before, after, f"a/{left_path}", f"b/{right_path}")
    print(diff or "(no content diff)")
    return 0


def normalize_asset_path(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"


def cmd_gateway_serve(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    server = serve_gateway(config.paths.root, host=args.host, port=args.port, token=args.token)
    print(f"Praxile gateway serving http://{args.host}:{args.port}")
    print(f"Console: http://{args.host}:{args.port}/")
    print(f"Project: {config.paths.root}")
    if args.token:
        print("Auth: token required")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def cmd_doctor(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    router = ModelRouter(config)
    profile = inspect_project(config.paths.root)
    git = GitEnv(config)
    shell = ShellEnv(config, SafetyPolicy(config))
    tests = TestEnv(config, shell)
    index_health = store.index_status()
    project_map = build_project_map(config, max_dirs=60, max_files=80)
    print(f"Project: {config.paths.root}")
    print(f"Harness: {config.paths.state}")
    print(f"Config: {config.paths.config}")
    print(f"Runtime mode: {config.get('runtime', 'mode')}")
    policy = interop_policy(config)
    print("Checks:")
    print(f"- config exists: {config.paths.config.exists()}")
    print(f"- sqlite index exists: {config.paths.db.exists()}")
    print(f"- sqlite fts available: {index_health.get('fts_available')}")
    print(
        f"- index assets: {index_health.get('assets_indexed')}/"
        f"{index_health.get('assets_expected')} indexed"
    )
    print(f"- index vectors: {index_health.get('vectors_indexed')} indexed")
    print(f"- index needs rebuild: {index_health.get('needs_rebuild')}")
    if index_health.get("missing"):
        print(f"- index missing assets: {', '.join(index_health['missing'][:5])}")
    if index_health.get("stale"):
        print(f"- index stale assets: {', '.join(index_health['stale'][:5])}")
    print(f"- git repository: {git.state().get('is_repo')}")
    configured = config.get("runtime", "default_test_commands", default=[])
    detected = profile.test_commands
    active = tests.detect_commands()
    print(f"- detected stacks: {', '.join(profile.stacks) if profile.stacks else '(none)'}")
    print(f"- detected markers: {', '.join(profile.markers) if profile.markers else '(none)'}")
    print(f"- detected package manager: {profile.package_manager or '(none)'}")
    print(f"- configured verification commands: {', '.join(configured) if configured else '(none)'}")
    print(f"- suggested verification commands: {', '.join(detected) if detected else '(none)'}")
    print(f"- active verification commands: {', '.join(active) if active else '(none)'}")
    print(f"- missing verification tools: {', '.join(profile.missing_tools) if profile.missing_tools else '(none)'}")
    state_dirs = [
        config.paths.state / "memory",
        config.paths.state / "skills",
        config.paths.state / "evals",
        config.paths.state / "rules",
        config.paths.trajectories,
        config.paths.proposals_pending,
        config.paths.db.parent,
        config.paths.logs,
        config.paths.backups,
    ]
    missing_state = [str(path.relative_to(config.paths.root)) for path in state_dirs if not path.exists()]
    print(f"- state layout complete: {not missing_state}")
    if missing_state:
        print(f"- missing state paths: {', '.join(missing_state)}")
    print(
        f"- project map: {project_map.get('total_files')} visible files, "
        f"protected skipped={project_map.get('protected_skipped')}, truncated={project_map.get('truncated')}"
    )
    cache = project_map.get("cache", {})
    print(
        f"- project map cache: enabled={cache.get('enabled')} hit={cache.get('hit')} "
        f"ttl={cache.get('ttl_seconds')}s"
    )
    print(
        "- evolution LLM proposals: "
        f"enabled={config.get('evolution', 'llm_assisted_proposals', default=False)} "
        f"route={config.get('evolution', 'llm_model_role', default='evolution_model')}"
    )
    print(
        "- retrieval vector adapter: "
        f"enabled={config.get('retrieval', 'vector_enabled', default=False)} "
        f"hybrid={config.get('retrieval', 'hybrid_enabled', default=False)} "
        f"provider={config.get('retrieval', 'vector_provider', default=config.get('retrieval', 'embedding_provider', default='local_hash'))}"
    )
    vector_provider = config.get("retrieval", "vector_provider", default=config.get("retrieval", "embedding_provider", default="local_hash"))
    if config.get("retrieval", "vector_enabled", default=False) and vector_provider == "local_hash":
        print(
            "- note: retrieval.vector_provider=local_hash is a lightweight lexical-vector fallback; "
            "install praxile[vector] and set vector_provider=sentence_transformers for semantic embeddings."
        )
    print(
        "- browser adapter: "
        f"enabled={config.get('browser', 'enabled', default=False)} "
        f"mode={config.get('browser', 'current_mvp', default='human_acceptance_checklists_only')}"
    )
    print(f"- high-risk path signals: {len(project_map.get('high_risk_modules', []))}")
    for note in profile.notes:
        print(f"- note: {note}")
    print(f"- agent kind: {policy['agent']['kind']}")
    print(f"- agent state root: {policy['agent']['state_root']}")
    print(f"- external framework autoloads .praxile skills: {policy['skills']['external_framework_autoloads_praxile_skills']}")
    print(f"- Praxile loads project skills: {policy['skills']['praxile_loads_project_skills']}")
    print(f"- External global memory auto-write: {policy['memory']['external_global_memory_write']}")
    print(f"- Trajectory compatibility sidecar: {policy['trajectory']['compat_sidecar']}")
    channels = ChannelSystem(config).list_bindings()
    print(f"- channel bindings: {len(channels)}")
    for binding in channels:
        print(f"  - {binding.id} platform={binding.platform} token_env={binding.token_env}")
    print("Adapter bridge:")
    for name, value in policy["agent"]["adapter_bridge"]["capabilities"].items():
        status = "available" if value["available"] else "not detected"
        print(f"- {name}: {status}")
    print("Model routes:")
    print("Model roles:")
    if not has_configured_models(config):
        print("- not configured: run `praxile setup` to configure a provider and model roles")
    for role_name, role in config.get("model_roles", default={}).items():
        if not isinstance(role, dict):
            continue
        provider_name = str(role.get("provider") or "")
        known = provider_name == "local" or provider_name in router.providers
        print(f"- {role_name}: {provider_name}:{role.get('model')} (provider_known={known})")
    for key, value in config.get("routing", default={}).items():
        if isinstance(value, str):
            provider_name = value.split(":", 1)[0] if ":" in value else value
            known = provider_name in router.providers
            print(f"- {key}: {value} (provider_known={known})")
    exit_code = 0
    if args.online:
        timeout = config.get("runtime", "online_check_timeout_seconds", default=8)
        print(f"Online model checks (timeout={timeout}s per unique route target):")
        checks = router.check_routes(timeout_seconds=timeout)
        if not checks:
            print("- no model routes configured")
            print("Run `praxile setup` first, then re-run `praxile doctor --online`.")
            exit_code = 1
        for check in checks:
            keys = ", ".join(check["route_keys"])
            print(
                f"- {keys}: {check['target']} -> {check['status']} "
                f"({check['latency_ms']}ms)"
            )
            print(f"  {check['detail']}")
        required_keys = {
            "default_model",
            "planning_model",
            "coding_model",
            "evolution_model",
            "model_roles.coding_agent",
            "model_roles.experience_reflection",
            "model_roles.reward_judge",
            "model_roles.proposal_composer",
        }
        failed_required = [
            check
            for check in checks
            if check["status"] != "ok" and any(key in required_keys for key in check["route_keys"])
        ]
        if failed_required:
            print("Model reachability failed for one or more required routes.")
            exit_code = 1
    else:
        print("- online model checks: skipped (use `praxile doctor --online`)")
    print("Allowed command prefixes:")
    for prefix in config.get("safety", "allowed_command_prefixes", default=[]):
        print(f"- {prefix}")
    return exit_code


def cmd_interop(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    print(format_interop_policy(interop_policy(config)))
    return 0


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
                applied = store.apply_proposal(proposal)
                accepted += 1
                print(f"Accepted {applied['proposal_id']}: {applied['title']}")
                break
            if choice in {"r", "reject"}:
                reason = safe_input("Reject reason (optional): ").strip() or None
                record_duplicate_warning_decision(store, proposal, guidance, "rejected")
                rejected_proposal = store.reject_proposal(proposal, reason=reason)
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


if __name__ == "__main__":
    raise SystemExit(main())
