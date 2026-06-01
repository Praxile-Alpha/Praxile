from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


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

def cmd_search(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    kinds = [str(item) for item in getattr(args, "kind", []) or []] or None
    results = store.retrieve(args.query, kinds=kinds, limit=max(1, int(args.limit or 6)))
    if getattr(args, "json", False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        print("No matching experience assets found.")
        return 0
    for item in results:
        score = item.get("final_score", item.get("score", ""))
        mode = item.get("retrieval_mode") or "keyword"
        print(f"{item.get('path')}  kind={item.get('kind') or item.get('type')}  score={score}  mode={mode}")
        why = item.get("why_loaded") or item.get("reason")
        if why:
            print(f"  {shorten(str(why), 220)}")
    return 0

def cmd_propose(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    trajectory = store.latest_trajectory() if args.trajectory_id == "latest" else store.get_trajectory(args.trajectory_id)
    if not trajectory:
        print(f"No trajectory found: {args.trajectory_id}", file=sys.stderr)
        return 1
    proposals = EvolutionEngine(config, router=ModelRouter(config)).generate(trajectory)
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
            "proposal_gate": proposal.get("proposal_gate"),
            "target_files": proposal.get("target_files", []),
        }
        for proposal in proposals
    ]
    store.update_trajectory(trajectory)
    if getattr(args, "dry_run", False):
        print(f"Generated {len(proposals)} proposal candidate(s) from {trajectory.get('task_id')} (dry run).")
        for proposal in proposals:
            print(f"- {proposal['proposal_id']} {proposal['type']} {proposal['risk_level']} {proposal['title']}")
        return 0
    for proposal in proposals:
        store.write_proposal(proposal)
    print(f"Wrote {len(proposals)} pending proposal(s) from {trajectory.get('task_id')}.")
    if proposals:
        print(f"Review with: praxile review --source-run {trajectory.get('task_id')}")
    return 0

__all__ = [name for name in globals() if name.startswith("cmd_")]
