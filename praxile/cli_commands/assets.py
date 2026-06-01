from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_snapshot_create(args: argparse.Namespace, project_root: Path) -> int:
    config = Config.load(project_root)
    config.paths.state.mkdir(parents=True, exist_ok=True)
    snapshot = SnapshotManager(config.paths.state).create_snapshot(
        reason=str(getattr(args, "reason", "") or "manual snapshot"),
        source={"type": "manual_cli"},
    )
    print(f"Created snapshot {snapshot['snapshot_id']}")
    print(snapshot["path"])
    return 0

def cmd_snapshot_list(args: argparse.Namespace, project_root: Path) -> int:
    config = Config.load(project_root)
    snapshots = SnapshotManager(config.paths.state).list_snapshots()
    if not snapshots:
        print("No snapshots.")
        return 0
    for item in snapshots:
        print(f"{item['snapshot_id']}  {item.get('created_at') or ''}  {item.get('reason') or ''}")
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

__all__ = [name for name in globals() if name.startswith("cmd_")]
