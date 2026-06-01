from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


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

def cmd_eval_run(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    suite = EvalSuite.load(Path(args.suite).expanduser().resolve())
    runner = EvalRunner(config, store)
    report = runner.run(suite)
    output_path = Path(args.output).expanduser().resolve() if args.output else None
    saved = runner.save_report(report, output_path)
    report["report_path"] = str(saved)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Eval suite: {report['suite']}")
        print(f"Run: {report['eval_run_id']}")
        print(f"Cases: {report['case_count']}")
        print(f"Average score: {report['average_score']}")
        print(f"Status: {'passed' if report['passed'] else 'failed'}")
        for case in report["cases"]:
            print(f"- {case['name']}: score={case['score']} {'passed' if case['passed'] else 'failed'}")
        print(f"Report: {saved}")
    return 0 if report["passed"] else 1

__all__ = [name for name in globals() if name.startswith("cmd_")]
