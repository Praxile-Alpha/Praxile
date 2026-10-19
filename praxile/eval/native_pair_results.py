"""Collect official native pair scores in the control plane, without model calls."""
import argparse
import json
import os
from pathlib import Path

from .compare_imports import compare_imports
from .contracts import BenchmarkOutcome
from .jobbench_results import import_trial as import_jobbench
from .workbuddy_results import import_trial as import_workbuddy
from .native_pair_run import load_pair, read_json


def task_checksum(path):
    from dirhash import dirhash
    return dirhash(path, "sha256")


def collect_pair(root):
    root, plan, arms, snapshots = load_pair(root, for_execution=False)
    state, state_digest = read_json(root / "execution.json")
    if (state.get("schema_version") != "praxile.native_pair_execution.v1"
            or state.get("plan_sha256") != snapshots["manifest.json"]):
        raise ValueError("execution record does not match frozen plan")
    checks = state.get("input_checks", [])
    expected_checks = [(arm, phase) for arm in ("baseline", "candidate") for phase in ("before", "after")]
    if (state.get("status") != "jobs_returned" or "local_inputs" not in plan
            or [(item.get("arm"), item.get("phase")) for item in checks] != expected_checks
            or any(item.get("scope") != "plan_and_local_inputs" or item.get("status") != "unchanged" for item in checks)):
        raise ValueError("comparison requires returned jobs and complete unchanged local input checks")
    workbuddy = arms["baseline"]["agents"][0]["import_path"].endswith(":WorkBuddyPraxileAgent")
    benchmark = "workbuddy" if workbuddy else "jobbench"
    tasks = {}
    for task in arms["baseline"]["tasks"]:
        path = Path(task["path"])
        if path.name in tasks:
            raise ValueError("duplicate task identity in pair")
        tasks[path.name] = (path, task_checksum(path))
    records, bindings, inputs, outcomes, retention = {}, [], [], [], []
    for arm, config in arms.items():
        job_dir = Path(config["jobs_dir"]) / config["job_name"]
        found = {}
        if job_dir.is_symlink():
            raise ValueError("symlink job output is not supported")
        for trial_path in sorted(job_dir.glob("*/result.json")):
            if trial_path.resolve() != trial_path.absolute():
                raise ValueError("symlink trial output is not supported")
            trial, trial_digest = read_json(trial_path)
            name = trial.get("task_name", "")
            prefix = benchmark + "/"
            if not isinstance(name, str) or not name.startswith(prefix) or name[len(prefix):] not in tasks:
                raise ValueError("unexpected task in arm output")
            task_id = name[len(prefix):]
            if task_id in found:
                raise ValueError("duplicate trial for planned task")
            path, checksum = tasks[task_id]
            if workbuddy:
                report = import_workbuddy(task_id, trial_path.parent, expected_task_checksum=checksum)
            else:
                rubric, _ = read_json(path / "tests/RUBRICS.json")
                report = import_jobbench(task_id, trial_path.parent, expected_task_checksum=checksum,
                    expected_rubric_weights=tuple(item["weight"] for item in rubric["rubrics"]))
            binding = report["native_trace_binding"]
            output = report.get("output_retention")
            if output is not None:
                retention.append({"arm": arm, "task_id": task_id, "status": output["status"],
                    "file_count": len(output["files"]), "files_sha256": output.get("files_sha256"),
                    "manifest_sha256": output.get("manifest_sha256"), "content_verified": False})
            expected_mode = "fresh_disabled" if arm == "baseline" else "development_candidate"
            outcomes.append({"arm": arm, **report["outcome"]})
            if binding.get("status") == "bound" and binding.get("experience_mode") != expected_mode:
                raise ValueError("official report is not bound to the expected native arm")
            if binding.get("status") == "bound" and arm == "candidate":
                native, _ = read_json(trial_path.parent / "agent/praxile-result.json")
                if native.get("development_context", {}).get("candidate_digest") != plan["candidate_digest"]:
                    raise ValueError("trial used a different development candidate")
            found[task_id] = report if binding.get("status") == "bound" else {
                **report, "outcome": BenchmarkOutcome(task_id, "unsupported").to_dict()}
            bindings.append({"arm": arm, "task_id": task_id, **binding})
            inputs.append({"arm": arm, "task_id": task_id, "trial_sha256": trial_digest,
                           "evidence_digests": report["evidence_digests"]})
        records[arm] = []
        for task_id, (_, checksum) in tasks.items():
            records[arm].append(found.get(task_id) or {
                "schema_version": "praxile." + benchmark + "_import.v1", "task_checksum": checksum,
                "execution_status": "missing_trial", "outcome": BenchmarkOutcome(task_id, "infra_error").to_dict()})
    comparison = compare_imports([records])
    return {"schema_version": "praxile.native_pair_report.v1", "plan_sha256": snapshots["manifest.json"],
        "execution_sha256": state_digest, "benchmark": benchmark, "bindings": bindings, "inputs": inputs,
        "comparison": comparison, "official_outcomes": outcomes,
        "output_retention": retention,
        "promotion_eligible": False, "controlled_experiment_verified": False,
        "limitations": ["Local file consistency is not source attestation or immutable runtime isolation.",
                        "Official rubric details remain in private trial files, not this report.",
                        "Remote model/judge identities and full costs are not verified by this collector."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect_pair(args.pair_dir)
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(report, handle, indent=2)
    print("Compared " + str(report["comparison"]["sample_count"]) + " complete task pairs; no promotion granted.")


if __name__ == "__main__":
    main()
