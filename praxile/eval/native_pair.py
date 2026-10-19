"""Prepare development-only native Harbor pairs from one shared job config."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re

from .native_context import load_candidate, plan_candidate


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def prepare_pair(job, candidate, *, repository, output_dir, require_new_outputs=True):
    """Freeze configuration equality, not the external environment or causality."""
    if not isinstance(job, dict):
        raise ValueError("job must be an object")
    shared = deepcopy(job)
    name = shared.get("job_name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,170}", name):
        raise ValueError("explicit safe job_name required")
    agents = shared.get("agents")
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        raise ValueError("pair requires exactly one native agent")
    agent = agents[0]
    if agent.get("import_path") not in {
            "praxile.adapters.harbor_native:PraxileAgent",
            "praxile.adapters.harbor_native:WorkBuddyPraxileAgent"}:
        raise ValueError("unsupported native executor")
    kwargs = agent.get("kwargs")
    if not isinstance(kwargs, dict) or not agent.get("model_name"):
        raise ValueError("explicit model and worker configuration required")
    if set(kwargs) & {"development_candidate_file", "development_candidate_sha256", "development_only", "repository"}:
        raise ValueError("base job must not already contain a development override")
    for key in ("max_steps", "timeout_seconds"):
        if type(kwargs.get(key)) is not int or kwargs[key] <= 0:
            raise ValueError("explicit positive integer worker budgets required")
    tasks = shared.get("tasks")
    if (not isinstance(tasks, list) or not tasks or shared.get("datasets")
            or any(not isinstance(task, dict) or not isinstance(task.get("path"), str)
                   or not Path(task["path"]).is_absolute() for task in tasks)):
        raise ValueError("pair requires explicit tasks, not dynamic datasets")
    if shared.get("source_jobs") or shared.get("install_only") or shared.get("orchestrator"):
        raise ValueError("regrade, install-only and legacy orchestrator jobs are unsupported")
    if not isinstance(shared.get("jobs_dir"), str) or not Path(shared["jobs_dir"]).is_absolute():
        raise ValueError("absolute jobs_dir required")
    planning = plan_candidate(candidate, "Preparation only", repository, development_only=True)
    shared.update(n_attempts=1, n_concurrent_trials=1, retry={"max_retries": 0})
    jobs = {}
    for arm in ("baseline", "candidate"):
        config = deepcopy(shared)
        config["job_name"] = name + "-" + arm
        run_path = Path(shared["jobs_dir"]) / config["job_name"]
        if require_new_outputs and (run_path.exists() or run_path.is_symlink()):
            raise ValueError("paired job already exists; never overwrite or silently resume")
        if arm == "candidate":
            config["agents"][0]["kwargs"].update(
                development_candidate_file=str(Path(output_dir).resolve() / "candidate-asset.json"),
                development_candidate_sha256=digest(candidate),
                development_only=True, repository=repository)
        jobs[arm] = config
    return {"schema_version": "praxile.native_pair_plan.v1", "track": "development",
        "repository": repository, "candidate_digest": planning["candidate_digest"],
        "candidate_file_sha256": digest(candidate), "shared_config_sha256": digest(shared),
        "arms": {arm: {"config_file": arm + ".json", "config_sha256": digest(config),
                       "job_name": config["job_name"]} for arm, config in jobs.items()},
        "execution_started": False, "controlled_experiment_verified": False,
        "promotion_eligible": False,
        "limitations": ["Configuration equality does not freeze mutable files, image tags, endpoints or judge state.",
                        "Task scheduling order is owned by Harbor, not verified by this preparation step.",
                        "Requires separate execution, official scoring and trace binding for each arm."]}, jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--freeze-local-inputs", action="store_true",
                        help="Snapshot task trees and worker wheels for verification before each arm")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    candidate = load_candidate(args.candidate, args.candidate_sha256)
    job = json.loads(args.job.read_bytes())
    plan, jobs = prepare_pair(job, candidate, repository=args.repository, output_dir=output)
    if args.freeze_local_inputs:
        from .native_input_snapshot import snapshot_inputs
        plan["schema_version"] = "praxile.native_pair_plan.v2"
        plan["local_inputs"] = snapshot_inputs(jobs["baseline"])
    # Exclusive directory creation leaves any interrupted plan visibly incomplete.
    # Write the manifest last; never overwrite a previous experiment definition.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, value in [("candidate-asset.json", candidate), *[(arm + ".json", config) for arm, config in jobs.items()],
                        ("manifest.json", plan)]:
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(encoded(value))
    print("Prepared development pair; no executor or judge started.")


if __name__ == "__main__":
    main()
