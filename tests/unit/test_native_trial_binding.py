import hashlib
import json

import pytest

from praxile.eval.jobbench_results import import_trial as jobbench_import
from praxile.eval.workbuddy_results import import_trial as workbuddy_import
from praxile.trace import native_run_to_events


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(params=["jobbench", "workbuddy"])
def case(tmp_path, request):
    result = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
        "trajectory": {"task_id": "native-task", "start_time": "2026-10-19T00:00:00Z",
                       "result": {"status": "completed"}}}
    digest = write(tmp_path, "agent/praxile-result.json", result)
    write(tmp_path, "agent/praxile-events.json", {
        "schema_version": "praxile.native_event_projection.v1", "source_result_sha256": digest,
        "events": [e.to_dict() for e in native_run_to_events(result)]})
    trial = {"task_name": request.param + "/public-task", "task_checksum": "frozen",
        "agent_result": {"metadata": {"praxile": {"source_result_sha256": digest,
            "trajectory_task_id": "native-task", "experience_mode": "fresh_disabled"}}}}
    write(tmp_path, "result.json", trial)
    def run():
        kwargs = {"expected_task_checksum": "frozen"}
        if request.param == "jobbench":
            kwargs["expected_rubric_weights"] = (1,)
        return (jobbench_import if request.param == "jobbench" else workbuddy_import)(
            "public-task", tmp_path, **kwargs)
    return tmp_path, trial, run


def test_binds_native_identity_without_inventing_official_score(case):
    _, _, run = case
    report = run()
    binding = report["native_trace_binding"]
    assert binding["status"] == "bound"
    assert binding["trajectory_task_id"] == "native-task"
    assert binding["event_count"] > 0
    assert binding["provenance_attested"] is False
    assert report["outcome"]["task_id"] == "public-task"
    assert report["outcome"]["status"] == "infra_error"
    assert report["outcome"]["scores"] == []
    assert "agent/praxile-events.json" in report["evidence_digests"]


def test_legacy_trial_is_explicitly_unbound_even_with_nearby_files(case):
    root, trial, run = case
    del trial["agent_result"]["metadata"]["praxile"]["source_result_sha256"]
    write(root, "result.json", trial)
    report = run()
    assert report["native_trace_binding"]["status"] == "unbound"
    assert "agent/praxile-result.json" not in report["evidence_digests"]


def test_official_score_is_preserved_with_binding(case):
    root, trial, run = case
    trial.update(finished_at="2026-10-19T00:01:00Z", verifier_environment_mode="separate",
                 verifier_result={"rewards": {"reward": 1}})
    write(root, "verifier/reward.json", {"reward": 1})
    if trial["task_name"].startswith("jobbench/"):
        write(root, "verifier/verification.json", {"status": "ok", "reward": 1})
        write(root, "verifier/judge-details.json", {"total_score": 1, "max_score": 1,
            "total_count": 1, "rubrics": [{"index": 0, "weight": 1,
                "result": {"passed": True, "score": 1}}]})
    else:
        trial["config"] = {"verifier": {"import_path": "workbuddy_bench.judge:CompositeVerifier"}}
        write(root, "verifier/score.json", {"reward": 1, "test_status": "full_pass",
            "judges": [], "verdicts": []})
    write(root, "result.json", trial)
    report = run()
    assert report["native_trace_binding"]["status"] == "bound"
    assert report["outcome"]["status"] == "completed"
    assert report["outcome"]["scores"][0]["value"] == 1
    assert report["native_trace_binding"]["provenance_attested"] is False


@pytest.mark.parametrize("mutation", ["source", "projection", "identity", "mode", "digest", "metadata", "symlink"])
def test_rejects_cross_trial_or_modified_native_evidence(case, mutation):
    root, trial, run = case
    native = trial["agent_result"]["metadata"]["praxile"]
    if mutation == "source":
        path = root / "agent/praxile-result.json"
        path.write_text(path.read_text() + " ")
    elif mutation == "projection":
        path = root / "agent/praxile-events.json"
        projection = json.loads(path.read_text())
        projection["events"] = []
        write(root, "agent/praxile-events.json", projection)
    elif mutation == "symlink":
        path = root / "agent/praxile-result.json"
        path.rename(root / "other.json")
        path.symlink_to(root / "other.json")
    elif mutation == "metadata":
        trial["agent_result"]["metadata"] = []
    else:
        field = {"identity": "trajectory_task_id", "mode": "experience_mode", "digest": "source_result_sha256"}[mutation]
        native[field] = "wrong"
    write(root, "result.json", trial)
    with pytest.raises(ValueError):
        run()


@pytest.mark.parametrize("change,expected", [
    ("none", "matched"), ("bytes", "mismatch"), ("size", "mismatch"),
    ("missing", "mismatch"), ("added", "mismatch"), ("incomplete", "unknown"),
    ("duplicate", "unknown"), ("escape", "unknown"), ("no_hash", "unknown"),
    ("wrong_directory", "unknown"), ("missing_inventory", "unknown")])
def test_import_compares_bound_inventory_to_retained_bytes(case, change, expected):
    root, trial, run = case
    target = root / "artifacts/workspace/output"
    target.mkdir(parents=True)
    (target / "a.txt").write_bytes(b"abc")
    write(root, "artifacts/manifest.json", [{"source": "/workspace/output",
        "destination": "artifacts/workspace/output", "type": "directory", "status": "ok"}])
    trial["config"] = {"agent": {"kwargs": {"output_directory": "output"}}}
    result = json.loads((root / "agent/praxile-result.json").read_text())
    inventory = {"schema_version": "praxile.artifact_inventory.v1", "complete": True,
        "directory": "output", "files": [{"path": "output/a.txt", "size_bytes": 3,
        "sha256": hashlib.sha256(b"abc").hexdigest(), "status": "unsupported"}]}
    result["artifact_inventory"] = inventory
    if change == "bytes":
        (target / "a.txt").write_bytes(b"xyz")
    elif change == "size":
        inventory["files"][0]["size_bytes"] = 4
    elif change == "missing":
        (target / "a.txt").unlink()
    elif change == "added":
        (target / "b.txt").write_bytes(b"extra")
    elif change == "incomplete":
        inventory["complete"] = False
    elif change == "duplicate":
        inventory["files"] *= 2
    elif change == "escape":
        inventory["files"][0]["path"] = "output/../a.txt"
    elif change == "no_hash":
        del inventory["files"][0]["sha256"]
    elif change == "wrong_directory":
        inventory["directory"] = "elsewhere"
    elif change == "missing_inventory":
        del result["artifact_inventory"]
    digest = write(root, "agent/praxile-result.json", result)
    trial["agent_result"]["metadata"]["praxile"]["source_result_sha256"] = digest
    write(root, "agent/praxile-events.json", {
        "schema_version": "praxile.native_event_projection.v1", "source_result_sha256": digest,
        "events": [e.to_dict() for e in native_run_to_events(result)]})
    write(root, "result.json", trial)
    report = run()
    identity = report["native_trace_binding"]["artifact_identity"]
    assert identity["status"] == expected
    assert identity["content_verified"] is False
    assert identity["verifier_input_verified"] is False
    assert identity["provenance_attested"] is False
    assert report["outcome"]["scores"] == []
    if change in {"bytes", "size"}:
        assert identity["changed"] == ["a.txt"]
    if change == "missing":
        assert identity["missing"] == ["a.txt"]
    if change == "added":
        assert identity["added"] == ["b.txt"]
