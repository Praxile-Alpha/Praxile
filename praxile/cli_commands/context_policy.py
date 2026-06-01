from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_sync(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    service = RepositoryContextService(context.config, context.store)
    github_requested = bool(args.github or args.github_online)
    include_docs = bool(args.docs or not (args.docs or args.specs or args.ci or github_requested))
    include_specs = bool(args.specs or not (args.docs or args.specs or args.ci or github_requested))
    iterations = int(args.iterations or 0) if args.watch else 1
    if iterations < 0:
        raise ValueError("--iterations must be >= 0")
    passes: list[dict[str, Any]] = []
    pass_index = 0
    while True:
        pass_index += 1
        snapshot = service.sync(
            write=not bool(args.dry_run),
            refresh_map=bool(args.refresh_map or not args.dry_run),
            since=args.since,
            include_docs=include_docs,
            include_specs=include_specs,
            include_ci=bool(args.ci),
            include_github=github_requested,
            github_fetch=bool(args.github_online),
        )
        passes.append(snapshot)
        if not args.watch or (iterations and pass_index >= iterations):
            break
        time.sleep(max(0.1, float(args.interval or 60.0)))
    snapshot = passes[-1]
    if args.watch:
        snapshot = {**snapshot, "watch": {"passes": len(passes), "interval": args.interval, "iterations": args.iterations}}
    if getattr(args, "json", False):
        print(json.dumps(snapshot if not args.watch else {"latest": snapshot, "passes": passes}, indent=2, ensure_ascii=False))
    else:
        print(format_context_status(snapshot))
        if args.dry_run:
            print("Dry run: no context snapshot was written.")
    return 0

def cmd_context_status(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = ContextJuiceService(context.config, context.store).status()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_context_juice_status(payload))
    return 0

def cmd_context_compress(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    service = ContextJuiceService(context.config, context.store)
    selected = [name for name, value in {"run": args.run, "source": args.source, "text": args.text}.items() if value]
    if len(selected) > 1:
        raise ValueError("Choose only one of --run, --source, or --text.")
    write = not bool(args.no_write)
    role = args.role or "coding_agent"
    if args.source:
        payload = service.compress_file(args.source, role=role, write=write)
    elif args.text:
        payload = service.compress_text(args.text, source_type="text", role=role, source_id="cli", write=write)
    else:
        payload = service.compress_run(args.run or "latest", role=role if args.role else "proposal_composer", write=write)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_context_compression(payload))
    return 0

def cmd_context_tree(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = RepositoryMemoryTreeService(context.config, context.store).build(
        module=args.module,
        recent=args.recent,
        write=not bool(args.no_write),
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_memory_tree(payload))
    return 0

def cmd_policy_list(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = PolicyService(context.config).list_layers()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_policy_layers(payload))
    return 0

def cmd_policy_check(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = PolicyService(context.config).check(write_defaults=bool(args.write_defaults))
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_policy_check(payload))
    return 0 if payload.get("ok") else 1

def cmd_policy_explain(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = PolicyService(context.config).explain(args.topic)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_policy_explain(payload))
    return 0

def cmd_watch(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    iterations = int(args.iterations or 0)
    if iterations < 0:
        raise ValueError("--iterations must be >= 0")
    service = GovernanceLoopService(context.config, context.store)
    reports: list[dict[str, Any]] = []
    pass_index = 0
    while True:
        pass_index += 1
        reports.append(
            service.run_once(
                write=not bool(args.dry_run),
                compress=bool(args.compress),
                rebuild_graph=not bool(args.no_graph),
                run_audit=not bool(args.no_audit),
                run_reflect=bool(args.reflect),
                write_reflect_proposals=bool(args.write_reflect_proposals),
            )
        )
        if args.once or (iterations and pass_index >= iterations):
            break
        time.sleep(max(0.1, float(args.interval or 60.0)))
    payload = reports[-1]
    if len(reports) > 1:
        payload = {**payload, "watch": {"passes": len(reports), "interval": args.interval}}
    if args.json:
        print(json.dumps(payload if len(reports) == 1 else {"latest": payload, "reports": reports}, indent=2, ensure_ascii=False))
    else:
        print(format_governance_report(payload))
        if len(reports) > 1:
            print(f"Watch loop completed {len(reports)} pass(es).")
    return 0

def cmd_workflow_list(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = WorkflowService(context.config).list()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_workflow_list(payload))
    return 0

def cmd_workflow_show(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    payload = WorkflowService(context.config).show(args.name)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_workflow_show(payload))
    return 0

def cmd_workflow_seed(args: argparse.Namespace, project_root: Path) -> int:
    context = load_command_context(project_root)
    written = WorkflowService(context.config).seed(overwrite=bool(args.overwrite))
    payload = {"written": written, "count": len(written)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Seeded {len(written)} workflow template(s).")
        for path in written:
            print(f"- {path}")
    return 0

__all__ = [name for name in globals() if name.startswith("cmd_")]
