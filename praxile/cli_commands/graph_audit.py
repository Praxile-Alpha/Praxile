from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_graph_status(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    graph = GraphService(store)
    rebuild_result = graph.rebuild() if getattr(args, "rebuild", False) else None
    status = graph.status()
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
    result = GraphService(store).rebuild()
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
    graph = GraphService(store)
    if getattr(args, "rebuild", False):
        graph.rebuild()
    report = graph.explain(args.ref, depth=args.depth, limit=args.limit)
    if not report.get("found") and not getattr(args, "rebuild", False):
        graph.rebuild()
        report = graph.explain(args.ref, depth=args.depth, limit=args.limit)
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
    report = AuditService(config, store).run(args.id, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)

def cmd_audit_asset(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = AuditService(config, store).asset(args.path, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)

def cmd_audit_proposal(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = AuditService(config, store).proposal(args.proposal_id, rebuild_graph=args.rebuild_graph, redaction=args.redaction)
    return emit_audit_report(report, args)

def cmd_audit_bundle(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = AuditService(config, store).bundle(
        limit_runs=args.limit_runs,
        rebuild_graph=args.rebuild_graph,
        redaction=args.redaction,
        include_reflect=args.include_reflect,
        reflect_limit=args.reflect_limit,
    )
    return emit_audit_report(report, args)

def cmd_audit_check(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = AuditService(config, store).check(
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

__all__ = [name for name in globals() if name.startswith("cmd_")]
