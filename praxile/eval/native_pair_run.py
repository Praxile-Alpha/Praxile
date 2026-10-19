"""Explicit one-shot sequential execution of prepared native development pairs."""
import argparse
import asyncio
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path

from .native_context import load_candidate
from .native_pair import encoded, prepare_pair
from .native_input_snapshot import snapshot_inputs
from ..utils import write_json


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("pair inputs must be regular files")
    raw = path.read_bytes()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate pair JSON field")
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique)
    encoded(value)  # Reject nonfinite values before handing anything to Harbor.
    return value, hashlib.sha256(raw).hexdigest()


def load_pair(root):
    root = Path(root).resolve(strict=True)
    plan, plan_hash = read_json(root / "manifest.json")
    if not isinstance(plan, dict) or plan.get("schema_version") not in {
            "praxile.native_pair_plan.v1", "praxile.native_pair_plan.v2"}:
        raise ValueError("unsupported native pair plan")
    candidate = load_candidate(root / "candidate-asset.json", plan.get("candidate_file_sha256"))
    baseline, baseline_hash = read_json(root / "baseline.json")
    proposed, proposed_hash = read_json(root / "candidate.json")
    if not isinstance(baseline, dict) or not isinstance(baseline.get("job_name"), str):
        raise ValueError("invalid baseline configuration")
    if not baseline["job_name"].endswith("-baseline"):
        raise ValueError("invalid baseline job identity")
    base = dict(baseline, job_name=baseline["job_name"][:-len("-baseline")])
    expected, arms = prepare_pair(base, candidate, repository=plan.get("repository"), output_dir=root)
    if plan["schema_version"] == "praxile.native_pair_plan.v2":
        expected["schema_version"] = plan["schema_version"]
        expected["local_inputs"] = snapshot_inputs(arms["baseline"])
    if encoded(expected) != encoded(plan) or encoded(arms["candidate"]) != encoded(proposed):
        raise ValueError("pair manifest or arm configuration differs from frozen plan")
    if encoded(arms["baseline"]) != encoded(baseline):
        raise ValueError("baseline configuration differs from frozen plan")
    snapshots = {"manifest.json": plan_hash, "baseline.json": baseline_hash,
                 "candidate.json": proposed_hash, "candidate-asset.json": plan["candidate_file_sha256"]}
    return root, plan, arms, snapshots


def harbor_backend(arms):
    expected = ("0.18.0" if arms["baseline"]["agents"][0]["import_path"].endswith(":WorkBuddyPraxileAgent")
                else "0.22.0")
    if version("harbor") != expected:
        raise ValueError("paired execution requires Harbor " + expected)
    from harbor.job import Job
    from harbor.models.job.config import JobConfig
    configs = {arm: JobConfig.model_validate(config) for arm, config in arms.items()}
    return Job.create, configs, expected


class InputIntegrityError(ValueError):
    """Execution evidence is confounded by changed or unreadable inputs."""


def verify_inputs(root, plan, config, snapshots):
    try:
        for name, checksum in snapshots.items():
            if read_json(root / name)[1] != checksum:
                raise InputIntegrityError("pair inputs changed after execution claim")
        if "local_inputs" in plan and encoded(snapshot_inputs(config)) != encoded(plan["local_inputs"]):
            raise InputIntegrityError("local task or worker inputs changed during experiment")
    except InputIntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InputIntegrityError("input integrity could not be verified") from exc


async def run_pair(root):
    root, plan, arms, snapshots = load_pair(root)
    create, configs, harbor_version = harbor_backend(arms)
    state_path = root / "execution.json"
    state = {"schema_version": "praxile.native_pair_execution.v1", "status": "starting",
        "plan_sha256": snapshots["manifest.json"], "harbor_version": harbor_version,
        "order": ["baseline", "candidate"], "arms": {}, "promotion_eligible": False,
        "local_inputs_frozen": "local_inputs" in plan,
        "input_checks": [],
        "controlled_experiment_verified": False}
    # Exclusive durable claim prevents duplicate calls, including after crashes.
    with os.fdopen(os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(encoded(state))
        handle.flush()
        os.fsync(handle.fileno())
    current = None
    def check_inputs(phase):
        record = {"arm": current, "phase": phase,
                  "scope": "plan_and_local_inputs" if "local_inputs" in plan else "plan_files_only",
                  "status": "unverified"}
        state["input_checks"].append(record)
        try:
            verify_inputs(root, plan, arms[current], snapshots)
        except InputIntegrityError:
            record["status"] = "failed"
            raise
        record["status"] = "unchanged"

    try:
        for current in state["order"]:
            check_inputs("before")
            output = Path(arms[current]["jobs_dir"]) / arms[current]["job_name"]
            if output.exists() or output.is_symlink():
                raise ValueError("arm output already exists; implicit resume forbidden")
            state["status"] = "running"
            state["arms"][current] = {"status": "running", "job_dir": str(output)}
            write_json(state_path, state)
            job = await create(configs[current])
            await job.run()
            # Harbor returning can include failed tasks: never label it resolved.
            state["arms"][current]["status"] = "job_returned"
            check_inputs("after")
            write_json(state_path, state)
        state["status"] = "jobs_returned"
    except BaseException as exc:
        state["status"] = "interrupted" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "failed"
        if isinstance(exc, InputIntegrityError):
            state["status"] = "confounded"
        state["error_type"] = type(exc).__name__
        state["stopped_at_arm"] = current
        if state["arms"].get(current, {}).get("status") == "running":
            state["arms"][current]["status"] = state["status"]
            state["arms"][current]["resource_cleanup_verified"] = False
        write_json(state_path, state)
        raise
    write_json(state_path, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Explicitly start model and verifier calls")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required; this can consume model and verifier credits")
    state = asyncio.run(run_pair(args.pair_dir))
    print("Pair state: " + state["status"] + "; official score import still required.")


if __name__ == "__main__":
    main()
