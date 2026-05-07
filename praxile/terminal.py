from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .channels import ChannelSystem
from .consolidation import ConsolidationEngine
from .config import Config
from .runtime import AgentRuntime
from .store import ExperienceStore
from .utils import shorten


class TerminalSession:
    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.config = Config.load(self.project_root)
        self.store = ExperienceStore(self.config.paths)
        self.store.initialize(self.config)

    def banner(self) -> str:
        return (
            "Praxile Terminal\n"
            f"Project: {self.config.paths.root}\n"
            f"State: {self.config.paths.state}\n"
            "Type `help` for commands, `exit` to leave."
        )

    def handle(self, line: str) -> tuple[bool, str]:
        text = line.strip()
        if not text:
            return True, ""
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return True, f"Parse error: {exc}"
        if not parts:
            return True, ""

        command, args = parts[0], parts[1:]
        if command in {"exit", "quit", ":q"}:
            return False, "Leaving Praxile Terminal."
        if command in {"help", "?"}:
            return True, self.help_text()
        if command == "status":
            return True, self.status()
        if command == "run":
            return True, self.run_task(args)
        if command == "review":
            return True, self.review(args[0] if args else None)
        if command in {"proposals", "pending"}:
            return True, self.proposals()
        if command == "consolidate":
            return True, self.consolidate()
        if command == "accept":
            if not args:
                return True, "Usage: accept <PROPOSAL_ID>"
            return True, self.accept(args[0])
        if command == "reject":
            if not args:
                return True, "Usage: reject <PROPOSAL_ID>"
            return True, self.reject(args[0])
        if command == "history":
            limit = int(args[0]) if args else 10
            return True, self.history(limit)
        if command == "channels":
            return True, self.channels()
        if command == "memory":
            query = " ".join(args)
            return True, self.memory(query)
        return True, f"Unknown command: {command}\n\n{self.help_text()}"

    def help_text(self) -> str:
        return (
            "Commands:\n"
            "  run <task> [--dry-run]        Run an agent task\n"
            "  review [TASK_OR_PROPOSAL_ID]  Show latest trajectory or proposal\n"
            "  proposals                     List pending proposals\n"
            "  consolidate                   Generate duplicate-experience cleanup proposals\n"
            "  accept <PROPOSAL_ID>          Apply a pending proposal\n"
            "  reject <PROPOSAL_ID>          Reject a pending proposal\n"
            "  history [LIMIT]               List recent trajectories\n"
            "  memory [QUERY]                Search local memory/rules/skills\n"
            "  channels                      List Telegram/Discord bindings\n"
            "  status                        Show project and model route status\n"
            "  exit                          Leave the terminal"
        )

    def status(self) -> str:
        channels = ChannelSystem(self.config).list_bindings()
        routes = self.config.get("routing", default={}) or {}
        route_lines = [f"- {key}: {value}" for key, value in routes.items() if isinstance(value, str)]
        return (
            f"Project: {self.config.paths.root}\n"
            f"Config: {self.config.paths.config}\n"
            f"State: {self.config.paths.state}\n"
            f"Channels: {len(channels)}\n"
            "Routes:\n"
            + ("\n".join(route_lines) if route_lines else "- none")
        )

    def run_task(self, args: list[str]) -> str:
        task, test_commands, max_steps, dry_run = self._parse_run_args(args)
        if not task:
            return "Usage: run <task> [--test-command CMD] [--max-steps N] [--dry-run]"
        trajectory = AgentRuntime(self.config).run(
            task,
            test_commands=test_commands or None,
            max_steps=max_steps,
            dry_run=dry_run,
        )
        return self._format_run_result(trajectory)

    def review(self, item_id: str | None) -> str:
        proposal = self.store.find_proposal(item_id) if item_id else None
        if proposal:
            return self._format_proposal(proposal)
        trajectory = self.store.get_trajectory(item_id) if item_id else self.store.latest_trajectory()
        if not trajectory:
            return "No trajectory or proposal found."
        return self._format_trajectory(trajectory)

    def proposals(self) -> str:
        proposals = self.store.list_proposals(status="pending")
        if not proposals:
            return "No pending proposals."
        lines = ["Pending proposals:"]
        for item in proposals:
            targets = ", ".join(item.get("target_files", [])[:3])
            lines.append(
                f"- {item['proposal_id']} [{item['type']}] "
                f"risk={item.get('risk_level', 'low')} confidence={item.get('confidence', 'n/a')}"
            )
            lines.append(f"  {item.get('title', '')}")
            lines.append(f"  targets: {targets or '(none)'}")
        lines.append("Use `review <PROPOSAL_ID>`, `accept <PROPOSAL_ID>`, or `reject <PROPOSAL_ID>`.")
        return "\n".join(lines)

    def consolidate(self) -> str:
        proposals = ConsolidationEngine(self.config, self.store).generate()
        if not proposals:
            return "No consolidation proposals generated."
        lines = []
        for proposal in proposals:
            self.store.write_proposal(proposal)
            lines.append(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
        lines.append("Use `review <PROPOSAL_ID>` before accepting.")
        return "\n".join(lines)

    def accept(self, proposal_id: str) -> str:
        proposal = self.store.find_proposal(proposal_id, status="pending")
        if not proposal:
            return "No pending proposal found."
        accepted = self.store.apply_proposal(proposal)
        paths = "\n".join(f"- {item['path']}" for item in accepted.get("applied_changes", []))
        return f"Accepted {accepted['proposal_id']}: {accepted['title']}\n{paths}".rstrip()

    def reject(self, proposal_id: str) -> str:
        proposal = self.store.find_proposal(proposal_id, status="pending")
        if not proposal:
            return "No pending proposal found."
        rejected = self.store.reject_proposal(proposal)
        return f"Rejected {rejected['proposal_id']}: {rejected['title']}"

    def history(self, limit: int) -> str:
        rows = self.store.list_history(limit=limit)
        if not rows:
            return "No task history yet."
        return "\n".join(
            f"{row['task_id']}  {row['status']}  reward={row['reward_score']}  {row['created_at']}  {row['user_task']}"
            for row in rows
        )

    def channels(self) -> str:
        bindings = ChannelSystem(self.config).list_bindings()
        if not bindings:
            return "No channel bindings yet."
        default_id = self.config.get("channels", "default")
        return "\n".join(
            f"{binding.id}{' default' if binding.id == default_id else ''}  "
            f"platform={binding.platform} mode={binding.mode} kind={binding.kind} token_env={binding.token_env}"
            for binding in bindings
        )

    def memory(self, query: str) -> str:
        if not query:
            query = ""
        results = self.store.retrieve(query, limit=10)
        if not results:
            return "No memory, skill, eval, or rule matches."
        return "\n\n".join(
            f"- {item['kind']} {item['path']} priority={item.get('load_priority')}\n"
            f"  {shorten(item.get('snippet', ''), 500)}"
            for item in results
        )

    def _parse_run_args(self, args: list[str]) -> tuple[str, list[str], int | None, bool]:
        task_parts: list[str] = []
        test_commands: list[str] = []
        max_steps: int | None = None
        dry_run = False
        i = 0
        while i < len(args):
            token = args[i]
            if token == "--test-command" and i + 1 < len(args):
                test_commands.append(args[i + 1])
                i += 2
                continue
            if token == "--max-steps" and i + 1 < len(args):
                max_steps = int(args[i + 1])
                i += 2
                continue
            if token == "--dry-run":
                dry_run = True
                i += 1
                continue
            task_parts.append(token)
            i += 1
        return " ".join(task_parts).strip(), test_commands, max_steps, dry_run

    def _format_run_result(self, trajectory: dict[str, Any]) -> str:
        lines = [
            f"Task: {trajectory['task_id']}",
            f"Status: {trajectory['result']['status']}",
            f"Summary: {trajectory['result']['summary']}",
            f"Reward overall: {trajectory.get('reward_report', {}).get('overall')}",
        ]
        if trajectory.get("dry_run"):
            lines.insert(2, "Mode: dry-run")
        proposals = trajectory.get("experience_candidates", [])
        if proposals:
            lines.append("Pending proposals:")
            lines.extend(f"- {item['proposal_id']} [{item['type']}] {item['title']}" for item in proposals)
        lines.append("Next: review, accept <PROPOSAL_ID>, or run another task.")
        return "\n".join(lines)

    def _format_trajectory(self, trajectory: dict[str, Any]) -> str:
        actions = "\n".join(
            f"- #{item.get('step')} {item.get('action_type')} -> {item.get('status')}"
            for item in trajectory.get("actions", [])
        )
        proposals = "\n".join(
            f"- {item['proposal_id']} [{item['type']}] {item['title']}"
            for item in trajectory.get("experience_candidates", [])
        )
        return (
            f"Task: {trajectory.get('task_id')}\n"
            f"User task: {trajectory.get('user_task')}\n"
            f"Status: {trajectory.get('result', {}).get('status')}\n"
            f"Summary: {trajectory.get('result', {}).get('summary')}\n"
            f"Reward overall: {trajectory.get('reward_report', {}).get('overall')}\n\n"
            f"Actions:\n{actions or '- none'}\n\n"
            f"Proposals:\n{proposals or '- none'}"
        )

    def _format_proposal(self, proposal: dict[str, Any]) -> str:
        targets = "\n".join(f"- .praxile/{path}" for path in proposal.get("target_files", []))
        evidence = "\n".join(f"- {item}" for item in proposal.get("evidence", []))
        return (
            f"Proposal: {proposal['proposal_id']}\n"
            f"Type: {proposal['type']}\n"
            f"Status: {proposal['status']}\n"
            f"Risk: {proposal['risk_level']}\n"
            f"Confidence: {proposal.get('confidence', 'n/a')}\n"
            f"Title: {proposal['title']}\n"
            f"Reason: {proposal['reason']}\n\n"
            f"Evidence:\n{evidence or '- none'}\n\n"
            f"Targets:\n{targets or '- none'}\n\n"
            f"Diff:\n{shorten(proposal.get('diff', ''), 8000) or '(none)'}"
        )
