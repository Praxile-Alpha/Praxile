from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_init(args: argparse.Namespace, project_root: Path) -> int:
    config = Config.load(project_root)
    store = ExperienceStore(config.paths)
    config_exists = config.paths.config.exists()
    profile = None if args.no_detect else inspect_project(config.paths.root)
    if profile:
        config.data.setdefault("project", {})["detected_stacks"] = profile.stacks
        config.data.setdefault("project", {})["detected_markers"] = profile.markers
        config.data.setdefault("project", {})["detected_package_manager"] = profile.package_manager
        config.data.setdefault("project", {})["detected_test_commands"] = profile.test_commands
    seeded_commands = args.test_command or []
    if not seeded_commands and profile and (args.force or not config_exists):
        seeded_commands = profile.test_commands
    if seeded_commands:
        config.data.setdefault("runtime", {})["default_test_commands"] = seeded_commands
    store.initialize(config, force=args.force)
    if args.wizard:
        run_setup_wizard(args, config)
    if args.force or not config_exists or profile or args.test_command or args.wizard:
        config.write()
    print(f"Initialized {config.paths.state}")
    print(f"Config: {config.paths.config}")
    if profile:
        print(f"Detected stacks: {', '.join(profile.stacks) if profile.stacks else '(none)'}")
        print(f"Detected markers: {', '.join(profile.markers) if profile.markers else '(none)'}")
        if profile.package_manager:
            print(f"Package manager: {profile.package_manager}")
        commands = config.get("runtime", "default_test_commands", default=[])
        print(f"Default verification commands: {', '.join(commands) if commands else '(none)'}")
        if profile.missing_tools:
            print(f"Missing tools for detected commands: {', '.join(profile.missing_tools)}")
    commands = config.get("runtime", "default_test_commands", default=[])
    example_test = commands[0] if commands else "python -m pytest"
    print("Next steps:")
    if not has_configured_models(config):
        print("1. Configure a model provider: praxile setup")
        print("2. Verify model reachability: praxile doctor --online")
        print(f"3. Run your first task: praxile run \"Fix the failing test\" --test-command {shlex.quote(example_test)}")
    else:
        print(f"1. Run your first task: praxile run \"Fix the failing test\" --test-command {shlex.quote(example_test)}")
        print("2. Review what Praxile learned: praxile review --interactive")
        print("3. Explain the experience loop: praxile explain latest")
    print("Tip: config is project-local in .praxile/config.json; API keys stay in environment variables.")
    return 0

def cmd_setup(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    run_setup_wizard(args, config)
    config.write()
    print(f"Updated {config.paths.config}")
    if has_configured_models(config):
        print("Next: run `praxile doctor --online` to test model reachability.")
    else:
        print("No model provider configured. Praxile can still run demos, inspect state, and manage proposals.")
    if ChannelSystem(config).list_bindings():
        print("Channel bindings:")
        for binding in ChannelSystem(config).list_bindings():
            print(f"- {binding.id} platform={binding.platform} token_env={binding.token_env}")
    return 0

def cmd_demo(args: argparse.Namespace, project_root: Path) -> int:
    demo_root = Path(args.path).resolve() if args.path else Path(tempfile.mkdtemp(prefix="praxile-demo-")).resolve()
    marker = demo_root / ".praxile-demo"
    if demo_root.exists() and any(demo_root.iterdir()) and not marker.exists() and not args.force:
        raise ValueError("demo --path must be empty, an existing Praxile demo directory, or use --force")
    demo_root.mkdir(parents=True, exist_ok=True)
    marker.write_text("This directory is owned by `praxile demo`.\n", encoding="utf-8")
    if args.fast:
        return run_fast_demo(args, demo_root)

    print("[1/6] Creating demo project")
    calculator = demo_root / "calculator.py"
    test_file = demo_root / "test_calculator.py"
    calculator.write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    test_file.write_text(
        "import unittest\n\n"
        "from calculator import add\n\n\n"
        "class CalculatorTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )

    config = Config.load(demo_root)
    config.data.setdefault("runtime", {})["default_test_commands"] = ["python -m unittest"]
    store = ExperienceStore(config.paths)
    store.initialize(config)
    config.write()

    safety = SafetyPolicy(config)
    fs = FileSystemEnv(config, safety)
    git = GitEnv(config)
    shell = ShellEnv(config, safety)
    tests = TestEnv(config, shell)
    project = ProjectEnv(config, fs, git, tests)
    task = "Demo: fix failing calculator unittest and learn from the repair"
    logger = TrajectoryLogger(task, project.snapshot(refresh=True))
    retrieved = store.retrieve("calculator unittest repair", limit=8)
    logger.set_loaded_context(retrieved)
    store.record_asset_usage(logger.task_id, retrieved, used_in_prompt=True)
    logger.set_task_analysis(
        {
            "task_type": "bugfix",
            "risk_level": "low",
            "architecture_gate_required": False,
            "ui_human_review_required": False,
            "privacy_sensitive": False,
            "high_risk": False,
            "confidence": 1.0,
            "reasons": ["Deterministic local demo task."],
            "signals": {"demo": True},
            "frozen_hits": [],
        }
    )
    logger.set_plan(
        [
            "Run the failing unittest to capture objective evidence.",
            "Read the buggy calculator implementation.",
            "Apply the smallest scoped fix.",
            "Run unittest again and generate reward/proposals.",
        ]
    )

    print("[2/6] Running failing test")
    before_results = tests.run(["python -m unittest"])
    for result in before_results:
        logger.add_action(
            action_type="run_test",
            input_data={"command": result.get("data", {}).get("command")},
            observation=result,
            status=result.get("status", "unknown"),
        )
    read_observation = fs.read_file("calculator.py").to_dict()
    logger.add_action(
        action_type="read_file",
        input_data={"path": "calculator.py"},
        observation=read_observation,
        status=read_observation.get("status", "unknown"),
    )
    print("[3/6] Applying scoped fix")
    edit_observation = fs.write_file(
        "calculator.py",
        "def add(left, right):\n    return left + right  # fixed by Praxile demo\n",
        task_id=logger.task_id,
        step=len(logger.data["actions"]) + 1,
    ).to_dict()
    logger.add_action(
        action_type="edit_file",
        input_data={"path": "calculator.py"},
        observation=edit_observation,
        status=edit_observation.get("status", "unknown"),
    )
    shutil.rmtree(demo_root / "__pycache__", ignore_errors=True)
    print("[4/6] Running verification")
    after_results = tests.run(["python -m unittest"])
    for result in after_results:
        logger.add_action(
            action_type="run_test",
            input_data={"command": result.get("data", {}).get("command")},
            observation=result,
            status=result.get("status", "unknown"),
        )
    logger.set_diff_summary(git.diff_summary())
    trajectory = logger.finish(status="completed", summary="Fixed the demo calculator bug and captured learning signals.")
    print("[5/6] Generating reward and proposals")
    report = RewardEngine(config).build_report(trajectory, after_results)
    trajectory["reward_report"] = report
    proposals = EvolutionEngine(config).generate(trajectory)
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
            "target_files": proposal["target_files"],
        }
        for proposal in proposals
    ]
    trajectory["evolution_summary"] = {
        "used_assets": len(trajectory.get("loaded_assets") or []),
        "used_asset_counts": {},
        "produced_proposals": len(proposals),
        "proposal_counts": _count_by(proposals, "type"),
        "proposal_risk_counts": _count_by(proposals, "risk_level"),
        "proposal_confidence_counts": _count_by(proposals, "confidence_level"),
        "experience_generation": report.get("experience_generation", {}),
        "review_command": f"praxile review --source-run {logger.task_id}",
    }
    store.record_trajectory(trajectory)
    store.update_asset_usage_outcome(logger.task_id, "success")
    for proposal in proposals:
        store.write_proposal(proposal)

    accepted_id = None
    retrieved_after_accept: list[dict] = []
    if args.accept_first:
        memory = next((proposal for proposal in proposals if proposal.get("type") == "memory_update"), None)
        if memory:
            accepted = ProposalService(store).accept(memory["proposal_id"], confirm=True)
            accepted_id = accepted["proposal_id"]
            retrieved_after_accept = store.retrieve("calculator unittest repair", kinds=["memory"], limit=5)

    print("[6/6] Showing next-run retrieval evidence")
    print(f"Praxile demo project: {demo_root}")
    print(f"Task: {trajectory['task_id']}")
    print(f"Before unittest: {before_results[0].get('status') if before_results else 'unknown'}")
    print(f"After unittest: {after_results[0].get('status') if after_results else 'unknown'}")
    print(f"Reward overall: {report.get('overall')}")
    print_run_evolution_summary(trajectory)
    if accepted_id:
        print(f"Accepted demo memory proposal: {accepted_id}")
        print(f"Retrieval after accept: {len(retrieved_after_accept)} memory match(es)")
    else:
        print("No proposal was auto-accepted. Run:")
        print(f"  praxile --project {demo_root} review --interactive")
    print(f"Explain with: praxile --project {demo_root} explain {trajectory['task_id']}")
    print("Next steps:")
    print(f"1. praxile --project {demo_root} explain latest")
    print(f"2. praxile --project {demo_root} review --interactive")
    print('3. Run `praxile run "your task" --test-command "python -m pytest"` in your own repo.')
    if getattr(args, "show_files", False):
        print_demo_files(demo_root)
    return 0

def cmd_doctor(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    router = ModelRouter(config)
    profile = inspect_project(config.paths.root)
    git = GitEnv(config)
    shell = ShellEnv(config, SafetyPolicy(config))
    tests = TestEnv(config, shell)
    index_health = store.index_status()
    project_map = build_project_map(config, max_dirs=60, max_files=80)
    print(f"Project: {config.paths.root}")
    print(f"Harness: {config.paths.state}")
    print(f"Config: {config.paths.config}")
    print(f"Runtime mode: {config.get('runtime', 'mode')}")
    policy = interop_policy(config)
    print("Checks:")
    print(f"- config exists: {config.paths.config.exists()}")
    print(f"- sqlite index exists: {config.paths.db.exists()}")
    print(f"- sqlite fts available: {index_health.get('fts_available')}")
    print(
        f"- index assets: {index_health.get('assets_indexed')}/"
        f"{index_health.get('assets_expected')} indexed"
    )
    print(f"- index vectors: {index_health.get('vectors_indexed')} indexed")
    print(f"- index needs rebuild: {index_health.get('needs_rebuild')}")
    if index_health.get("missing"):
        print(f"- index missing assets: {', '.join(index_health['missing'][:5])}")
    if index_health.get("stale"):
        print(f"- index stale assets: {', '.join(index_health['stale'][:5])}")
    print(f"- git repository: {git.state().get('is_repo')}")
    configured = config.get("runtime", "default_test_commands", default=[])
    detected = profile.test_commands
    active = tests.detect_commands()
    print(f"- detected stacks: {', '.join(profile.stacks) if profile.stacks else '(none)'}")
    print(f"- detected markers: {', '.join(profile.markers) if profile.markers else '(none)'}")
    print(f"- detected package manager: {profile.package_manager or '(none)'}")
    print(f"- configured verification commands: {', '.join(configured) if configured else '(none)'}")
    print(f"- suggested verification commands: {', '.join(detected) if detected else '(none)'}")
    print(f"- active verification commands: {', '.join(active) if active else '(none)'}")
    print(f"- missing verification tools: {', '.join(profile.missing_tools) if profile.missing_tools else '(none)'}")
    state_dirs = [
        config.paths.state / "memory",
        config.paths.state / "skills",
        config.paths.state / "evals",
        config.paths.state / "rules",
        config.paths.trajectories,
        config.paths.proposals_pending,
        config.paths.db.parent,
        config.paths.logs,
        config.paths.backups,
    ]
    missing_state = [str(path.relative_to(config.paths.root)) for path in state_dirs if not path.exists()]
    print(f"- state layout complete: {not missing_state}")
    if missing_state:
        print(f"- missing state paths: {', '.join(missing_state)}")
    print(
        f"- project map: {project_map.get('total_files')} visible files, "
        f"protected skipped={project_map.get('protected_skipped')}, truncated={project_map.get('truncated')}"
    )
    cache = project_map.get("cache", {})
    print(
        f"- project map cache: enabled={cache.get('enabled')} hit={cache.get('hit')} "
        f"ttl={cache.get('ttl_seconds')}s"
    )
    print(
        "- evolution LLM proposals: "
        f"enabled={config.get('evolution', 'llm_assisted_proposals', default=False)} "
        f"route={config.get('evolution', 'llm_model_role', default='evolution_model')}"
    )
    print(
        "- retrieval vector adapter: "
        f"enabled={config.get('retrieval', 'vector_enabled', default=False)} "
        f"hybrid={config.get('retrieval', 'hybrid_enabled', default=False)} "
        f"provider={config.get('retrieval', 'vector_provider', default=config.get('retrieval', 'embedding_provider', default='local_hash'))}"
    )
    vector_provider = config.get("retrieval", "vector_provider", default=config.get("retrieval", "embedding_provider", default="local_hash"))
    if config.get("retrieval", "vector_enabled", default=False) and vector_provider == "local_hash":
        print(
            "- note: retrieval.vector_provider=local_hash is a lightweight lexical-vector fallback; "
            "install praxile[vector] and set vector_provider=sentence_transformers for semantic embeddings."
        )
    print(
        "- browser adapter: "
        f"enabled={config.get('browser', 'enabled', default=False)} "
        f"mode={config.get('browser', 'current_mvp', default='human_acceptance_checklists_only')}"
    )
    print(f"- high-risk path signals: {len(project_map.get('high_risk_modules', []))}")
    for note in profile.notes:
        print(f"- note: {note}")
    print(f"- agent kind: {policy['agent']['kind']}")
    print(f"- agent state root: {policy['agent']['state_root']}")
    print(f"- external framework autoloads .praxile skills: {policy['skills']['external_framework_autoloads_praxile_skills']}")
    print(f"- Praxile loads project skills: {policy['skills']['praxile_loads_project_skills']}")
    print(f"- External global memory auto-write: {policy['memory']['external_global_memory_write']}")
    print(f"- Trajectory compatibility sidecar: {policy['trajectory']['compat_sidecar']}")
    channels = ChannelSystem(config).list_bindings()
    print(f"- channel bindings: {len(channels)}")
    for binding in channels:
        print(f"  - {binding.id} platform={binding.platform} token_env={binding.token_env}")
    print("Adapter bridge:")
    for name, value in policy["agent"]["adapter_bridge"]["capabilities"].items():
        status = "available" if value["available"] else "not detected"
        print(f"- {name}: {status}")
    print("Model routes:")
    print("Model roles:")
    if not has_configured_models(config):
        print("- not configured: run `praxile setup` to configure a provider and model roles")
    for role_name, role in config.get("model_roles", default={}).items():
        if not isinstance(role, dict):
            continue
        provider_name = str(role.get("provider") or "")
        known = provider_name == "local" or provider_name in router.providers
        print(f"- {role_name}: {provider_name}:{role.get('model')} (provider_known={known})")
    for key, value in config.get("routing", default={}).items():
        if isinstance(value, str):
            provider_name = value.split(":", 1)[0] if ":" in value else value
            known = provider_name in router.providers
            print(f"- {key}: {value} (provider_known={known})")
    exit_code = 0
    if args.online:
        timeout = config.get("runtime", "online_check_timeout_seconds", default=8)
        print(f"Online model checks (timeout={timeout}s per unique route target):")
        checks = router.check_routes(timeout_seconds=timeout)
        if not checks:
            print("- no model routes configured")
            print("Run `praxile setup` first, then re-run `praxile doctor --online`.")
            exit_code = 1
        for check in checks:
            keys = ", ".join(check["route_keys"])
            print(
                f"- {keys}: {check['target']} -> {check['status']} "
                f"({check['latency_ms']}ms)"
            )
            print(f"  {check['detail']}")
        required_keys = {
            "default_model",
            "planning_model",
            "coding_model",
            "evolution_model",
            "model_roles.coding_agent",
            "model_roles.experience_reflection",
            "model_roles.reward_judge",
            "model_roles.proposal_composer",
        }
        failed_required = [
            check
            for check in checks
            if check["status"] != "ok" and any(key in required_keys for key in check["route_keys"])
        ]
        if failed_required:
            print("Model reachability failed for one or more required routes.")
            exit_code = 1
    else:
        print("- online model checks: skipped (use `praxile doctor --online`)")
    print("Allowed command prefixes:")
    for prefix in config.get("safety", "allowed_command_prefixes", default=[]):
        print(f"- {prefix}")
    return exit_code

def cmd_interop(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    print(format_interop_policy(interop_policy(config)))
    return 0

def cmd_interop_import_jsonl(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    imported = GenericJSONLAdapter().import_file(Path(args.path).expanduser().resolve())
    trajectory = imported["trajectory"]
    proposals: list[dict[str, Any]] = []
    if args.generate_proposals or args.write_proposals:
        proposals = EvolutionEngine(config).generate(trajectory)
        trajectory["experience_candidates"] = proposals
    store.record_trajectory(trajectory)
    written = 0
    if args.write_proposals:
        for proposal in proposals:
            store.write_proposal(proposal)
            written += 1
    summary = {
        "task_id": trajectory.get("task_id"),
        "rows": imported.get("rows"),
        "source_path": imported.get("source_path"),
        "generated_proposals": len(proposals),
        "written_proposals": written,
    }
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(f"Imported external JSONL trajectory: {summary['task_id']}")
        print(f"Rows: {summary['rows']}")
        print(f"Generated proposals: {summary['generated_proposals']}")
        print(f"Written proposals: {summary['written_proposals']}")
    return 0

__all__ = [name for name in globals() if name.startswith("cmd_")]
