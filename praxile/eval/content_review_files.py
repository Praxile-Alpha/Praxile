"""Read-only consistency checks and comparison of durable content review runs."""
import argparse
import json
from pathlib import Path

from .content_review import _digest, _validate_inputs, output_units
from .content_review_compare import _checked, compare_content_reviews
from .content_review_run import validate_execution_binding


def _read(root, name):
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("review evidence must be regular local files")
    with path.open("rb") as handle:
        data = handle.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("review evidence exceeds limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate evidence field")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("nonfinite evidence value")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except RecursionError as error:
        raise ValueError("evidence nesting exceeds limit") from error
    if not isinstance(value, dict):
        raise ValueError("evidence must be a JSON object")
    return value


def load_review_run(directory):
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("review directory must be a local directory")
    manifest, inputs, report, trajectory = [_read(root, name) for name in (
        "manifest.json", "inputs.json", "report.json", "trajectory.json")]
    validate_execution_binding(manifest.get("execution_binding"), output_text=inputs.get("output_text"),
                               source_text=inputs.get("source_text"))
    if (manifest.get("schema_version") != "praxile.content_review_run.v1"
            or manifest.get("input_sha256") != _digest(json.dumps(inputs, sort_keys=True))
            or set(inputs) != {"source_text", "output_text", "requirements"}):
        raise ValueError("run input binding mismatch")
    identity = manifest.get("judge_identity")
    if not isinstance(identity, dict) or manifest.get("judge_identity_sha256") != _digest(json.dumps(identity, sort_keys=True)):
        raise ValueError("judge identity binding mismatch")
    plan, _ = _checked(report)
    if plan["citation_mode"] not in ("quote", "units") or not isinstance(report.get("groups"), list):
        raise ValueError("unsupported citation mode or groups")
    expected = _validate_inputs(inputs["source_text"], inputs["output_text"], inputs["requirements"],
        plan["role"], plan["private"], 1, plan["timeout_per_call"], plan["response_mode"], None)
    if (plan["source_text_sha256"] != _digest(inputs["source_text"])
            or plan["output_text_sha256"] != _digest(inputs["output_text"])
            or plan["requirements_sha256"] != _digest(json.dumps(list(expected.values()), sort_keys=True))
            or identity.get("role") != plan["role"] or identity.get("private") != plan["private"]
            or manifest.get("total_output_tokens") != plan["total_output_tokens"]
            or manifest.get("timeout_per_call") != plan["timeout_per_call"]):
        raise ValueError("plan is not bound to persisted input or conditions")
    size = manifest.get("batch_size")
    if type(size) is not int or not 1 <= size <= 32:
        raise ValueError("invalid persisted batch size")
    ids = list(expected)
    partitions = [ids[i:i + size] for i in range(0, len(ids), size)]
    allocations = manifest.get("output_allocations")
    if allocations is None:
        base, remainder = divmod(plan["total_output_tokens"], len(partitions))
        allocations = [base + (i < remainder) for i in range(len(partitions))]
    if (not isinstance(allocations, list) or any(type(value) is not int for value in allocations)
            or plan["allocation_policy"] != ("equal" if manifest.get("output_allocations") is None else "explicit")):
        raise ValueError("invalid persisted allocation")
    if plan["groups"] != [{"ids": group, "max_tokens": budget} for group, budget in zip(partitions, allocations)] or len(allocations) != len(partitions):
        raise ValueError("persisted allocation mismatch")
    batches = trajectory.get("content_review_batches")
    if not isinstance(batches, list) or len(batches) != 1 or batches[0] != report:
        raise ValueError("report and trajectory disagree")
    if trajectory.get("content_reviews") != report["groups"]:
        raise ValueError("group reports and trajectory disagree")
    units = {item["id"]: item for item in output_units(inputs["output_text"])}
    for declared, group in zip(plan["groups"], report["groups"]):
        requirements = [expected[key] for key in declared["ids"]]
        payload = {"source_text": inputs["source_text"], "requirements": requirements}
        payload.update({"output_units": list(units.values())} if plan["citation_mode"] == "units"
                       else {"output_text": inputs["output_text"]})
        if group.get("input_sha256") != _digest(json.dumps(payload, ensure_ascii=False, sort_keys=True)):
            raise ValueError("group input binding mismatch")
        for item in group.get("items", []):
            if item.get("source_quote") != expected[item["id"]]["source_quote"]:
                raise ValueError("source citation mismatch")
            if plan["citation_mode"] == "units":
                refs = item.get("output_refs")
                if (not isinstance(refs, list) or len(refs) > 8
                        or any(not isinstance(ref, str) or ref not in units for ref in refs)
                        or len(set(refs)) != len(refs)
                        or item.get("output_spans") != [units[ref] for ref in refs]
                        or (item["verdict"] in {"preserved", "contradicted"} and not refs)):
                    raise ValueError("output span binding mismatch")
            else:
                quote = item.get("output_quote")
                if not isinstance(quote, str) or quote not in inputs["output_text"] or (
                        item["verdict"] in {"preserved", "contradicted"} and not quote.strip()):
                    raise ValueError("output quote binding mismatch")
    return report, manifest["judge_identity_sha256"]


def compare_review_runs(baseline_directory, candidate_directory):
    baseline, left_identity = load_review_run(baseline_directory)
    candidate, right_identity = load_review_run(candidate_directory)
    result = compare_content_reviews(baseline, candidate,
        baseline_judge_sha256=left_identity, candidate_judge_sha256=right_identity)
    result["local_file_consistency_checked"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare_review_runs(args.baseline, args.candidate)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Review evidence is missing, unsupported or inconsistent; comparison refused.\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "compared" else 1


if __name__ == "__main__":
    raise SystemExit(main())
