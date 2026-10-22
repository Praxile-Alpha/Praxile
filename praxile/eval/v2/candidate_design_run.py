"""Durable opt-in candidate design, separate from experiment execution."""
from ...trajectory import TrajectoryLogger
from ...utils import file_lock, read_json, write_json
from .candidate_designer import design_development_candidates
from .candidate import ContextCandidate
from .configured_composer import configured_design_router
from .development_evidence import collect_development_evidence
from .experiment import _write_once
from .schema import EvalSchemaError


def run_candidate_design(config, event_store, *, design_id, experiment_id, arm,
                         development_task_ids, heldout_task_ids, applies_to, private=False, resume=False,
                         executor_profile=None, max_output_tokens=None, request_timeout_seconds=None, response_mode=None,
                         repair_attempts=None):
    _validate_repairs(repair_attempts)
    _validate_mode(response_mode)
    _validate_timeout(request_timeout_seconds)
    if max_output_tokens is not None and (type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 16384):
        raise EvalSchemaError("candidate output budget must be an integer in [256, 16384]")
    # Verify source membership before constructing the model request.
    bundle = collect_development_evidence(config.paths.state, event_store, experiment_id=experiment_id,
        arm=arm, development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids)
    return _run_design(config, bundle, design_id=design_id,
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids,
        applies_to=applies_to, private=private, resume=resume,
        executor_profile=executor_profile, max_output_tokens=max_output_tokens,
        request_timeout_seconds=request_timeout_seconds, response_mode=response_mode, repair_attempts=repair_attempts)


def run_native_candidate_design(config, result_path, *, expected_sha256, track,
                                design_id, development_task_ids, heldout_task_ids, applies_to,
                                private=False, resume=False, executor_profile=None, max_output_tokens=None,
                                request_timeout_seconds=None, public_task_context_path=None,
                                public_task_context_sha256=None, response_mode=None, repair_attempts=None):
    from .native_development_evidence import collect_native_evidence
    _validate_repairs(repair_attempts)
    _validate_mode(response_mode)
    _validate_timeout(request_timeout_seconds)
    if max_output_tokens is not None and (type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 16384):
        raise EvalSchemaError("candidate output budget must be an integer in [256, 16384]")
    bundle = collect_native_evidence(result_path, expected_sha256=expected_sha256,
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids, track=track)
    if (public_task_context_path is None) != (public_task_context_sha256 is None):
        raise EvalSchemaError("public task context requires both file and SHA256")
    if public_task_context_path is not None:
        from .public_task_context import load_public_task_context
        bundle["public_task_context"] = load_public_task_context(public_task_context_path,
            public_task_context_sha256, bundle["task_id"], heldout_task_ids)
        bundle["public_task_context_sha256"] = public_task_context_sha256
        bundle["public_task_context_provenance_attested"] = False
    return _run_design(config, bundle, design_id=design_id,
        development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids,
        applies_to=applies_to, private=private, resume=resume,
        executor_profile=executor_profile, max_output_tokens=max_output_tokens,
        request_timeout_seconds=request_timeout_seconds, response_mode=response_mode, repair_attempts=repair_attempts)


def _validate_repairs(value):
    if value is not None and (type(value) is not int or value not in (0, 1)):
        raise EvalSchemaError("candidate repair attempts must be 0 or 1")


def _validate_mode(value):
    if value is not None and value not in ("text", "function", "function_json_actions"):
        raise EvalSchemaError("unsupported candidate response mode")


def _validate_timeout(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 300):
        raise EvalSchemaError("candidate request timeout must be an integer in [1, 300]")


def _run_design(config, bundle, *, design_id, development_task_ids, heldout_task_ids,
                applies_to, private, resume, executor_profile, max_output_tokens, request_timeout_seconds, response_mode,
                repair_attempts, shared_chat_budget=None):
    if response_mode == "function_json_actions" and executor_profile is None:
        raise EvalSchemaError("JSON action submission requires an executor profile")
    budget_options = {"shared_chat_budget": shared_chat_budget} if shared_chat_budget is not None else {}
    with configured_design_router(config, cycle_id=design_id, applies_to=applies_to, private=private, **budget_options) as (router, identity):
        online = {"track": "online_stream", "source_ledger_sha256": bundle["ledger_sha256"]} if bundle.get("track") == "online_stream" else {}
        root = config.paths.state / "eval/v2/candidate_designs" / design_id
        plan = {"schema_version": "praxile.candidate_design_run.v1", "design_id": design_id,
                "source_evidence": bundle, "designer": {**identity, "implementation": "praxile.candidate_designer.v2"},
                "development_task_ids": sorted(set(development_task_ids)),
                "heldout_task_ids": sorted(set(heldout_task_ids)), "applies_to": applies_to}
        plan.update(online)
        if max_output_tokens is not None or request_timeout_seconds is not None:
            plan.update(schema_version="praxile.candidate_design_run.v2",
                        generation_budget={"max_output_tokens": max_output_tokens if max_output_tokens is not None else 4000,
                                           "timeout_seconds": request_timeout_seconds if request_timeout_seconds is not None else 60})
        if executor_profile is not None:
            from .executor_profile import profile_context
            plan["executor_capability"] = profile_context(executor_profile)
        if repair_attempts is not None:
            plan["validation_repair_budget"] = {"max_repairs": repair_attempts,
                "max_designer_calls": 1 + repair_attempts, "local_validation_failures_only": True}
        if response_mode is not None:
            plan["response_protocol"] = {"mode": response_mode}
            if response_mode in ("function", "function_json_actions"):
                from .candidate_response import candidate_response_contract
                plan["response_protocol"]["contract"] = candidate_response_contract(
                    ["event:" + item["event_id"] for item in bundle["evidence"]], plan.get("executor_capability"),
                    json_actions=response_mode == "function_json_actions")
        with file_lock(root / "design"):
            existed = (root / "manifest.json").exists()
            if existed and not resume:
                raise EvalSchemaError("candidate design already exists; resume explicitly")
            _write_once(root / "manifest.json", plan)
            generated = read_json(root / "generated.json", None)
            if generated is not None:
                return _finish(root, design_id, generated, online=online)
            attempt_path = root / "request-intent.json"
            if bundle["evidence"] and (existed or attempt_path.exists()):
                raise EvalSchemaError(
                    "design has no durable generation; prior request outcome is unknown. "
                    "Resume cannot resend a model request; use a new design ID")
            trajectory_path = root / "trajectory.json"
            previous = read_json(trajectory_path, None)
            logger = TrajectoryLogger.from_data(previous) if previous is not None else TrajectoryLogger(
                f"Design development candidates {design_id}", {"purpose": "proposal_composer"})
            if shared_chat_budget is not None:
                logger.data["shared_chat_budget_start"] = shared_chat_budget.status()
            if bundle["evidence"]:
                # Persist intent before entering any generator/provider code. A kill
                # may bypass finally, and absence of a response is not zero usage.
                intent = {"schema_version": "praxile.design_request_intent.v1",
                          "design_id": design_id, "status": "outcome_unknown",
                          "request_dispatched": None, "remote_usage_known": False,
                          "automatic_replay_allowed": False}
                write_json(attempt_path, intent)
                logger.data["candidate_design_request_intent"] = intent
                write_json(trajectory_path, logger.data)
            try:
                result = design_development_candidates(evidence=bundle["evidence"],
                    development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids,
                    applies_to=applies_to, design_id=design_id, router=router, logger=logger,
                    private=private, executor_profile=executor_profile,
                    response_mode=response_mode or "text",
                    repair_attempts=repair_attempts or 0,
                    attempt_checkpoint=lambda: write_json(trajectory_path, logger.data),
                    public_task_context=bundle.get("public_task_context"),
                    request_timeout_seconds=request_timeout_seconds if request_timeout_seconds is not None else 60,
                    max_output_tokens=max_output_tokens if max_output_tokens is not None else 4000) if bundle["evidence"] else {
                        "decision": "NO_ENTRY", "candidates": [], "composition": None, "promotion_eligible": False}
            finally:
                if shared_chat_budget is not None:
                    logger.data["shared_chat_budget_end"] = shared_chat_budget.status()
                write_json(trajectory_path, logger.data)
            # Persist the generation once, then materialize recoverable derived files.
            write_json(root / "generated.json", result)
            return _finish(root, design_id, result, online=online)


def _finish(root, design_id, result, *, online=None):
    files = []
    for candidate in [*result["candidates"], *([result["composition"]] if result["composition"] else [])]:
        candidate = ContextCandidate.from_dict(candidate).to_dict()
        filename = candidate["candidate_id"] + ".json"
        _write_once(root / filename, candidate)
        files.append(filename)
    report = {"schema_version": "praxile.candidate_design_report.v1", "design_id": design_id,
              "decision": result["decision"], "candidate_files": files, "promotion_eligible": False,
              "experiments_started": False}
    if online:
        report.update(online)
    if result.get("abstention") is not None:
        report["abstention"] = result["abstention"]
    write_json(root / "report.json", report)
    return report
