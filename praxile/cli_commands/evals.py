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


def cmd_harness_components(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    manifest = HarnessComponentRegistry(config).manifest()
    if args.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0
    print("Harness components:")
    for item in manifest["components"]:
        print(f"- {item['component_id']}: version={item['version']} {item['title']}")
    return 0


def cmd_harness_mine(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    rows = FailurePathologyMiner(config).mine()
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"Repeated failure pathologies: {len(rows)}")
        for item in rows:
            print(f"- {item['pathology_id']} component={item['component_id']} episodes={item['episode_count']} confidence={item['confidence']} {item['signature']}")
    return 0


def cmd_harness_propose(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    proposal = FailurePathologyMiner(config).propose(args.pathology_id)
    store.write_proposal(proposal)
    if args.json:
        print(json.dumps(proposal, indent=2, ensure_ascii=False))
    else:
        print(f"Proposed {proposal['proposal_id']}: {proposal['title']}")
        print(f"Component: {proposal['component_change']['component_id']}")
        print("Validate this candidate before explicit human promotion.")
    return 0


def cmd_harness_propose_routing(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    proposal = BoundedHarnessEvolution(config, store).routing_proposal()
    if not proposal:
        print("No measured routing pathology met the configured evidence threshold.")
        return 0
    store.write_proposal(proposal)
    print(json.dumps(proposal, indent=2, ensure_ascii=False) if args.json else f"Proposed {proposal['proposal_id']}: {proposal['title']}")
    return 0


def cmd_harness_manifest(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    path = config.paths.state / "experience" / "harness" / "active-manifest.json"
    manifest = read_json(path, {"schema_version": 1, "components": {}, "promotion_history": []})
    if args.json:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
    else:
        print("Promoted harness components:")
        for component_id, item in (manifest.get("components") or {}).items():
            print(f"- {component_id}: {item.get('active_version')} proposal={item.get('proposal_id')} status={(item.get('monitoring') or {}).get('status')}")
    return 0


def cmd_harness_monitor(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    trajectory = store.latest_trajectory() if args.run_id == "latest" else store.get_trajectory(args.run_id)
    if not trajectory:
        raise ValueError(f"Run not found: {args.run_id}")
    events = BoundedHarnessEvolution(config, store).monitor(trajectory, apply_rollback=bool(args.apply))
    print(json.dumps(events, indent=2, ensure_ascii=False) if args.json else f"Rollback trigger events: {len(events)}")
    return 0


def cmd_harness_export(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    include_private = bool(args.include_private)
    if include_private and not config.get("harness_evolution", "export_private_repository_content", default=False):
        raise PermissionError("Private repository experiment export is disabled by project policy")
    output = BoundedHarnessEvolution(config, store).export_bundle(
        args.proposal_id,
        Path(args.output).expanduser().resolve(),
        include_private=include_private,
    )
    print(f"Experiment bundle: {output}")
    return 0


def cmd_judge_calibrate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    runner = JudgeCalibrationRunner(config)
    report = runner.run(Path(args.suite).expanduser().resolve())
    store.record_judge_calibration(report)
    proposal = None
    if args.write_proposal:
        history = store.list_judge_calibrations(report["judge"], limit=20)
        proposal = runner.reward_policy_proposal(history)
        if proposal:
            store.write_proposal(proposal)
    if args.json:
        print(json.dumps({"report": report, "proposal": proposal}, indent=2, ensure_ascii=False))
    else:
        print(f"Judge calibration: {report['calibration_id']}")
        print(f"Cases: {report['case_count']} recall={report['recall']} precision={report['precision']}")
        print(f"Disagreement: {report['disagreement_rate']} abstention: {report['abstention_rate']}")
        print(f"Evidence coverage: {report['evidence_coverage']}")
        print(f"Report: {report['path']}")
        if proposal:
            print(f"Gated reward-policy proposal: {proposal['proposal_id']}")
        elif args.write_proposal:
            print("No proposal generated; repeated-miscalibration threshold was not reached.")
    return 0 if report["recall"] >= float(config.get("semantic_judges", "calibration", "min_recall", default=0.8)) else 1


def cmd_reward_explain(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    trajectory = store.latest_trajectory() if args.id == "latest" else store.get_trajectory(args.id)
    if not trajectory:
        raise ValueError(f"Run not found: {args.id}")
    result = store.reward_evidence_for_task(str(trajectory.get("task_id")))
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    profile = result.get("reward_profile") or {}
    graph = result.get("evidence_graph") or {}
    print(f"Run: {result['task_id']} overall={result.get('overall')}")
    print(f"Reward profile: {profile.get('profile_id')} version={profile.get('profile_version')}")
    print(f"Evidence coverage: {graph.get('coverage')} escalation={bool((result.get('escalation') or {}).get('required'))}")
    for claim in graph.get("claims") or []:
        print(f"- {claim.get('claim_type')}: {claim.get('value')} [{claim.get('provenance')}] evidence={len(claim.get('evidence_refs') or [])} status={claim.get('status')}")
    for reason in (result.get("escalation") or {}).get("reasons") or []:
        print(f"  escalation: {reason}")
    return 0


def cmd_proposal_validate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    report = ProposalService(store).validate(
        args.proposal_id,
        Path(args.suite).expanduser().resolve(),
        keep_workspaces=True if args.keep_workspaces else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        comparison = report.get("comparison") or {}
        print(f"Validation: {report['validation_id']}")
        print(f"Proposal: {report['proposal_id']}")
        print(f"Status: {report['status']}")
        if comparison:
            print(f"Baseline: {comparison.get('baseline_score')}")
            print(f"Candidate: {comparison.get('candidate_score')}")
            print(f"Delta: {comparison.get('score_delta')}")
            print(comparison.get("summary"))
        elif report.get("error"):
            print(report["error"])
    return 0 if report["status"] == "validated" else 1

__all__ = [name for name in globals() if name.startswith("cmd_")]
