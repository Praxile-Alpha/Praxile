from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


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

__all__ = [name for name in globals() if name.startswith("cmd_")]
