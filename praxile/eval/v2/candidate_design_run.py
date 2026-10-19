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
                         development_task_ids, heldout_task_ids, applies_to, private=False, resume=False):
    # Verify source membership before constructing the model request.
    bundle = collect_development_evidence(config.paths.state, event_store, experiment_id=experiment_id,
        arm=arm, development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids)
    with configured_design_router(config, cycle_id=design_id, applies_to=applies_to, private=private) as (router, identity):
        root = config.paths.state / "eval/v2/candidate_designs" / design_id
        plan = {"schema_version": "praxile.candidate_design_run.v1", "design_id": design_id,
                "source_evidence": bundle, "designer": {**identity, "implementation": "praxile.candidate_designer.v1"},
                "development_task_ids": sorted(set(development_task_ids)),
                "heldout_task_ids": sorted(set(heldout_task_ids)), "applies_to": applies_to}
        with file_lock(root / "design"):
            if (root / "manifest.json").exists() and not resume:
                raise EvalSchemaError("candidate design already exists; resume explicitly")
            _write_once(root / "manifest.json", plan)
            generated = read_json(root / "generated.json", None)
            if generated is not None:
                return _finish(root, design_id, generated)
            trajectory_path = root / "trajectory.json"
            previous = read_json(trajectory_path, None)
            logger = TrajectoryLogger.from_data(previous) if previous is not None else TrajectoryLogger(
                f"Design development candidates {design_id}", {"purpose": "proposal_composer"})
            try:
                result = design_development_candidates(evidence=bundle["evidence"],
                    development_task_ids=development_task_ids, heldout_task_ids=heldout_task_ids,
                    applies_to=applies_to, design_id=design_id, router=router, logger=logger,
                    private=private) if bundle["evidence"] else {
                        "decision": "NO_ENTRY", "candidates": [], "composition": None, "promotion_eligible": False}
            finally:
                write_json(trajectory_path, logger.data)
            # Persist the generation once, then materialize recoverable derived files.
            write_json(root / "generated.json", result)
            return _finish(root, design_id, result)


def _finish(root, design_id, result):
    files = []
    for candidate in [*result["candidates"], *([result["composition"]] if result["composition"] else [])]:
        candidate = ContextCandidate.from_dict(candidate).to_dict()
        filename = candidate["candidate_id"] + ".json"
        _write_once(root / filename, candidate)
        files.append(filename)
    report = {"schema_version": "praxile.candidate_design_report.v1", "design_id": design_id,
              "decision": result["decision"], "candidate_files": files, "promotion_eligible": False,
              "experiments_started": False}
    write_json(root / "report.json", report)
    return report
