from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_run(args: argparse.Namespace, project_root: Path) -> int:
    if not args.task and not args.resume:
        raise ValueError("run requires a task, or --resume <TASK_ID>")
    config, store = load(project_root)
    route_overrides = apply_run_overrides(args, config)
    store.initialize(config)
    workspace_mode = getattr(args, "workspace_mode", None) or str(config.get("workspace", "default_mode", default="in-place"))
    if workspace_mode != "in-place":
        return run_in_isolated_workspace(args, config, store, route_overrides, workspace_mode)
    trajectory = RunService(config, store).run(
        args.task or "",
        test_commands=args.test_command or None,
        max_steps=args.max_steps,
        dry_run=args.dry_run,
        resume=args.resume,
        spec_files=args.spec or None,
        parallel_readonly_explore=getattr(args, "parallel_readonly_explore", None),
        use_experience=not bool(getattr(args, "without_experience", False)),
    )
    print(f"Task: {trajectory['task_id']}")
    print(f"Status: {trajectory['result']['status']}")
    if route_overrides:
        print("Route overrides:")
        for key, value in route_overrides.items():
            print(f"- {key}: {value}")
    if trajectory.get("dry_run"):
        print("Mode: dry-run (edits and shell commands were blocked)")
    if (trajectory.get("experience_control") or {}).get("mode") == "withheld":
        print("Experience: withheld (retrieval-control baseline)")
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
            accepted = ProposalService(store).accept(proposal["proposal_id"], confirm=True)
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
    proposal = store.find_proposal(args.proposal_id)
    if not proposal:
        print("No proposal found.")
        return 1
    proposal_status = proposal.get("status") or "pending"
    if proposal_status in {"proposed", "inconclusive", "regressed"}:
        print(
            "Harness proposal is not validated. Run "
            f"`praxile proposal validate {proposal['proposal_id']} --suite <SUITE.json>` first."
        )
        return 1
    if proposal_status not in {"pending", "validated"}:
        print(f"Proposal cannot be accepted from status `{proposal_status}`.")
        return 1
    accepted = ProposalService(store).accept(proposal["proposal_id"], confirm=True)
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
            rejected = ProposalService(store).reject(proposal["proposal_id"], reason=reason)
            print(f"Rejected {rejected['proposal_id']}: {rejected['title']}")
        print(f"Rejected {len(pending)} proposal(s).")
        return 0
    if not args.proposal_id:
        raise ValueError("reject requires <PROPOSAL_ID>, or a batch flag such as --low-confidence")
    proposal = store.find_proposal(args.proposal_id, status="pending")
    if not proposal:
        print("No pending proposal found.")
        return 1
    rejected = ProposalService(store).reject(proposal["proposal_id"], reason=args.reason)
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

def cmd_rollback(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    snapshots = SnapshotManager(config.paths.state)
    if snapshots.has_snapshot(args.id):
        result = snapshots.rollback(args.id)
        store.initialize(config)
        store.reindex_all()
        append_jsonl(
            config.paths.logs / "rollback.jsonl",
            {"event": "snapshot_rollback", "snapshot_id": args.id, "result": result, "created_at": utc_now()},
        )
        print(f"Rolled back snapshot {result['snapshot_id']}")
        for item in result.get("restored", []):
            print(f"- restored {item}")
        return 0
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

__all__ = [name for name in globals() if name.startswith("cmd_")]
