from __future__ import annotations

import hashlib

from ..cli_common import *  # noqa: F401,F403
from ..adapters import AdapterPolicy, MiniSweAgentAdapter
from ..eval.v2 import BenchmarkEvalRunner, OfficialSWEbenchEvaluator, SWEbenchTaskLoader
from ..trace import EventStore


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


def cmd_eval_benchmark(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if args.resume and not args.run_id:
        raise ValueError("--resume requires --run-id")
    adapter = MiniSweAgentAdapter(
        model=args.model,
        model_class=args.model_class,
        config_specs=args.adapter_config,
        environment=(
            {"MSWEA_COST_TRACKING": args.cost_tracking}
            if args.cost_tracking != "default"
            else None
        ),
        default_timeout_seconds=args.timeout,
    )
    evaluator = OfficialSWEbenchEvaluator(timeout_seconds=args.timeout)
    adapter_available, adapter_detail = adapter.availability()
    if not adapter_available:
        raise RuntimeError(f"mini-SWE-agent is unavailable: {adapter_detail}; install praxile[benchmark]")
    evaluator_available, evaluator_detail = evaluator.availability()
    if not evaluator_available:
        raise RuntimeError(f"SWE-bench evaluator is unavailable: {evaluator_detail}")
    loader = SWEbenchTaskLoader(dataset_name=args.dataset_name, split=args.split)
    selection = {
        "instance_ids": args.instance_id,
        "development_size": args.development_size,
        "seed": args.seed,
    }
    task_set = (
        loader.load(Path(args.tasks).expanduser().resolve(), **selection)
        if args.tasks
        else loader.load_huggingface(**selection)
    )
    source_overrides: dict[str, Path] = {}
    for raw in args.source:
        repo, separator, path = raw.partition("=")
        if not separator or not repo.strip() or not path.strip():
            raise ValueError(f"invalid --source {raw!r}; expected REPO=PATH")
        source_overrides[repo.strip()] = Path(path).expanduser().resolve()
    budgets: dict[str, Any] = {"wall_timeout_seconds": args.timeout}
    if args.max_cost is not None:
        budgets["max_cost"] = args.max_cost
    policy = AdapterPolicy(
        policy_id="p0-baseline",
        version="1",
        budgets=budgets,
        settings={
            "allow_unattended_execution": True,
            "workspace_isolated": True,
            "seed": args.seed,
            "step_limit": args.step_limit,
        },
    )
    adapter_config_identity: list[dict[str, Any]] = []
    for spec in args.adapter_config:
        candidate = Path(spec).expanduser()
        if candidate.is_file():
            adapter_config_identity.append(
                {
                    "path": str(candidate.resolve()),
                    "content_digest": "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest(),
                }
            )
        else:
            adapter_config_identity.append({"spec": spec})
    with adapter:
        report = BenchmarkEvalRunner(config.paths.state, EventStore(config.paths)).run(
            task_set,
            adapter=adapter,
            evaluator=evaluator,
            policy=policy,
            model={
                "model_name_or_path": args.model,
                "provider": args.model.partition("/")[0] if "/" in args.model else None,
                "model_class": args.model_class,
                "cost_tracking": args.cost_tracking,
                "adapter_config": adapter_config_identity,
            },
            eval_run_id=args.run_id,
            resume=args.resume,
            keep_workspaces=args.keep_workspaces,
            source_overrides=source_overrides,
        )
    metrics = report["metrics"]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Benchmark run: {report['eval_run_id']}")
        print(f"Task set: {report['task_set']}")
        print(f"Completed: {metrics['completed_count']}/{metrics['task_count']}")
        print(f"Resolved: {metrics['resolved_count']} (rate={metrics['resolution_rate']})")
        print(f"Tokens: {metrics['tokens']}  cost={metrics['cost']}  tool_calls={metrics['tool_calls']}")
        print(f"Manifest: {report['manifest_path']}")
        print(f"Report: {config.paths.state / 'eval' / 'v2' / 'runs' / report['eval_run_id'] / 'report.json'}")
    complete = metrics["completed_count"] == metrics["task_count"]
    resolved = metrics["resolved_count"] == metrics["task_count"]
    return 0 if complete and resolved else 1


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
