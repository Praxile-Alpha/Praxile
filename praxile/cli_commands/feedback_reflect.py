from __future__ import annotations

from ..cli_common import *  # noqa: F401,F403


def cmd_feedback(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    if str(args.target or "").strip() == "auto":
        raw = str(args.text or getattr(args, "positive", None) or getattr(args, "negative", None) or "")
        classifier = FeedbackSemanticClassifier(config, ModelRouter(config))
        semantic = classifier.classify(raw, feedback_semantic_context(store))
        intents = semantic.get("feedback_events") or extract_feedback_intents(raw)
        if not intents:
            raise ValueError("feedback auto requires natural-language text")
        generated: list[dict] = []
        recorded = []
        for intent in intents:
            target_type, target_id = resolve_feedback_intent_target(store, intent)
            feedback = build_feedback(
                target_type=target_type,
                target_id=target_id,
                raw_text=str(intent.get("raw_text") or raw),
                sentiment=str(intent.get("sentiment") or "neutral"),
                feedback_type=str(intent.get("feedback_type") or "") or None,
                strength=float(intent.get("strength") or 0.0),
                effect=intent.get("effect") if isinstance(intent.get("effect"), dict) else None,
            )
            if intent.get("requires_confirmation"):
                feedback.setdefault("effect", {})["requires_confirmation"] = True
            if intent.get("semantic_reason"):
                feedback.setdefault("effect", {})["semantic_reason"] = intent.get("semantic_reason")
            if semantic.get("active"):
                feedback["semantic_classifier"] = {
                    "active": True,
                    "model_role": semantic.get("model_role"),
                    "provider": semantic.get("provider"),
                    "model": semantic.get("model"),
                    "route": semantic.get("route", {}),
                }
            path = store.record_feedback(feedback)
            recorded.append((feedback, path))
            if target_type == "run":
                update_run_reward_after_feedback(config, store, target_id)
            elif target_type == "asset" and feedback["sentiment"] == "negative":
                generated.append(write_asset_feedback_proposal(store, feedback))
        for feedback, path in recorded:
            print(f"Recorded feedback {feedback['feedback_id']} -> {path.relative_to(config.paths.root)}")
            print(
                f"Target: {feedback['target_type']} {feedback['target_id']} "
                f"sentiment={feedback['sentiment']} strength={feedback['strength']}"
            )
        if generated:
            print("Generated governed proposal(s) for durable asset changes:")
            for proposal in generated:
                print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
        return 0
    target_type, target_id = resolve_feedback_target(store, args)
    sentiment, raw_text = resolve_feedback_sentiment(args)
    feedback = build_feedback(
        target_type=target_type,
        target_id=target_id,
        raw_text=raw_text,
        sentiment=sentiment,
    )
    path = store.record_feedback(feedback)
    generated: list[dict] = []
    if target_type == "run":
        update_run_reward_after_feedback(config, store, target_id)
    elif target_type == "asset" and feedback["sentiment"] == "negative":
        generated.append(write_asset_feedback_proposal(store, feedback))
    print(f"Recorded feedback {feedback['feedback_id']} -> {path.relative_to(config.paths.root)}")
    print(
        f"Target: {feedback['target_type']} {feedback['target_id']} "
        f"sentiment={feedback['sentiment']} strength={feedback['strength']}"
    )
    if target_type == "run":
        reward = store.feedback_reward_for("run", target_id)
        print(f"Run user_feedback_reward: score={reward.get('score')} events={len(reward.get('events') or [])}")
    if generated:
        print("Generated governed proposal(s) for durable asset changes:")
        for proposal in generated:
            print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
    return 0

def cmd_consolidate(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    include_all = bool(getattr(args, "all", False) or getattr(args, "experience", False))
    explicit = any(
        bool(getattr(args, name, False))
        for name in ["duplicates", "stale", "conflicts", "low_value", "experience"]
    )
    checks = {
        "duplicates": include_all or getattr(args, "duplicates", False) or not explicit,
        "stale": include_all or getattr(args, "stale", False),
        "conflicts": include_all or getattr(args, "conflicts", False),
        "low_value": include_all or getattr(args, "low_value", False),
    }
    engine = ConsolidationEngine(config, store)
    if getattr(args, "summary", False):
        summary = engine.summary(**checks, stale_days=args.stale_days)
        print("Experience governance summary:")
        for key, value in summary.items():
            print(f"- {key}: {value}")
        return 0
    proposals = engine.generate(**checks, stale_days=args.stale_days)
    if not proposals:
        print("No consolidation proposals generated.")
        return 0
    for proposal in proposals:
        store.write_proposal(proposal)
        print(f"- {proposal['proposal_id']} [{proposal['type']}] {proposal['title']}")
    print("Review with: praxile review --pending")
    return 0

def cmd_reflect(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    ci_mode = bool(getattr(args, "ci", False))
    modes = [
        name
        for name in ["duplicates", "stale", "harmful", "silent_failures", "rejected_proposals", "high_value_patterns"]
        if getattr(args, name, False)
    ]
    if ci_mode and not modes:
        modes = ["duplicates", "stale", "harmful", "silent_failures", "rejected_proposals", "high_value_patterns"]
    since = args.since
    if ci_mode and not since:
        since = config.get("reflect", "ci", "default_since", default=None)
    scope = ReflectScope(
        since=since,
        asset=args.asset,
        modes=frozenset(modes),
        stale_days=args.stale_days,
    )
    report = ReflectService(config, store).run(scope, write_proposals=bool(args.write_proposals))
    if ci_mode:
        report["ci"] = build_reflect_ci_check(
            report,
            max_findings=_cli_or_config_int(args, "max_findings", config.get("reflect", "ci", "max_findings", default=None)),
            max_high_severity=_cli_or_config_int(
                args,
                "max_high_severity",
                config.get("reflect", "ci", "max_high_severity", default=0),
            ),
            max_generated_proposals=_cli_or_config_int(
                args,
                "max_generated_proposals",
                config.get("reflect", "ci", "max_generated_proposals", default=None),
            ),
        )
        _write_reflect_ci_artifacts(config, report, args)
    report_format = "summary" if getattr(args, "summary", False) else args.report
    if report_format == "json":
        rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    elif report_format == "markdown":
        rendered = format_reflect_markdown(report)
    else:
        rendered = format_reflect_summary(report)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered.rstrip() + "\n", encoding="utf-8")
        print(f"Wrote reflect report: {output_path}")
    print(rendered)
    if ci_mode:
        ci = report.get("ci") if isinstance(report.get("ci"), dict) else {}
        return int(ci.get("exit_code", 1))
    return 0

def cmd_mine_patterns(args: argparse.Namespace, project_root: Path) -> int:
    config, store = load(project_root)
    store.initialize(config)
    engine = EvolutionEngine(config)
    
    print("Mining patterns from historical episodes...")
    episodes = PatternMiner.load_all_episodes(config.paths.state)
    print(f"Loaded {len(episodes)} episodes.")
    
    router = ModelRouter(config)
    patterns = PatternMiner.update_index(config.paths.state, config=config, router=router)
    print(f"Mined {len(patterns)} candidate patterns.")
    for pattern in patterns[:10]:
        reasons = ", ".join(str(item) for item in pattern.get("match_reasons", [])[:4]) or "single-episode or weak similarity"
        print(
            f"- {pattern.get('pattern_id')} score={pattern.get('pattern_score')} "
            f"confidence={pattern.get('confidence')} evidence={len(pattern.get('episodes') or [])} "
            f"feedback_delta={pattern.get('confidence_adjustment_from_feedback', 0)} reasons={reasons}"
        )
        for reason in (pattern.get("semantic_reasons") or [])[:2]:
            print(f"  semantic: {reason}")
    
    hypotheses = HypothesisGenerator.generate(patterns)
    context = PatternMiner.load_feedback_context(config.paths.state)
    from ..semantic_judges import CounterexampleSemanticChecker

    semantic_checker = CounterexampleSemanticChecker(config, router)
    validated = CounterexampleChecker.validate(hypotheses, episodes, context, semantic_checker=semantic_checker)
    
    proposals = ProposalComposer.compose(validated, engine)
    
    if not proposals:
        print("No high-confidence project patterns generated.")
        return 0
        
    for proposal in proposals:
        store.write_proposal(proposal)
        print(f"Generated proposal {proposal['proposal_id']} [{proposal['type']}]: {proposal['title']}")
        
    print("Review with: praxile review --pending")
    return 0

__all__ = [name for name in globals() if name.startswith("cmd_")]
