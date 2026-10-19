import hashlib
import json

import pytest

from praxile.eval import native_pair_results as collector
from praxile.eval.native_pair import encoded, prepare_pair
from praxile.eval.native_input_snapshot import snapshot_inputs
from praxile.eval.native_context import plan_candidate
from praxile.eval.v2.candidate import ContextCandidate
from praxile.trace import native_run_to_events


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(params=["jobbench", "workbuddy"])
def case(tmp_path, monkeypatch, request):
    benchmark = request.param
    task = tmp_path / "task1"
    write(task / "tests/RUBRICS.json", {"rubrics": [{"weight": 1}], "private": "PRIVATE_RUBRIC"})
    wheel = tmp_path / "worker.whl"
    wheel.write_bytes(b"fixture")
    candidate = ContextCandidate(candidate_id="pair", version="1", title="Fixture",
        candidate_type="context_policy", context_item={"content": "inspect available files"},
        confidence=0, evidence_refs=("event:fixture",), expected_effect={"hypothesis": "fixture"},
        applies_to={"repositories": ["fixture/repo"], "task_signals": ["available project"],
                    "does_not_apply_when": ["unrelated task"]}).to_dict()
    root = tmp_path / "pair"
    root.mkdir()
    agent_class = "WorkBuddyPraxileAgent" if benchmark == "workbuddy" else "PraxileAgent"
    job = {"job_name": "paired", "jobs_dir": str(tmp_path / "jobs"), "tasks": [{"path": str(task)}],
        "agents": [{"import_path": "praxile.adapters.harbor_native:" + agent_class, "model_name": "fixture",
            "kwargs": {"max_steps": 2, "timeout_seconds": 60, "wheel_path": str(wheel),
                       "wheel_sha256": hashlib.sha256(b"fixture").hexdigest()}}]}
    plan, arms = prepare_pair(job, candidate, repository="fixture/repo", output_dir=root)
    plan.update(schema_version="praxile.native_pair_plan.v2", local_inputs=snapshot_inputs(job))
    plan_hash = write(root / "manifest.json", plan)
    write(root / "candidate-asset.json", candidate)
    for arm, config in arms.items():
        write(root / (arm + ".json"), config)
        trial_root = tmp_path / "jobs" / config["job_name"] / "trial"
        native = {"schema_version": "praxile.native_benchmark_run.v1", "experience_mode": "fresh_disabled",
            "trajectory": {"task_id": arm, "start_time": "2026-10-19T00:00:00Z", "result": {"status": "completed"}}}
        if arm == "candidate":
            context = plan_candidate(candidate, "available project", "fixture/repo", development_only=True)
            content = context.pop("content")
            native.update(experience_mode="development_candidate", development_context={**context, "injected": True})
            native["trajectory"]["experimental_context"] = {"content": content, "injected": True,
                "sha256": hashlib.sha256(content.encode()).hexdigest()}
        source = write(trial_root / "agent/praxile-result.json", native)
        write(trial_root / "agent/praxile-events.json", {"schema_version": "praxile.native_event_projection.v1",
            "source_result_sha256": source, "events": [e.to_dict() for e in native_run_to_events(native)]})
        trial = {"task_name": benchmark + "/task1", "task_checksum": "frozen", "finished_at": "done",
            "verifier_environment_mode": "separate", "verifier_result": {"rewards": {"reward": 1}},
            "config": {"verifier": {"import_path": "workbuddy_bench.judge:CompositeVerifier"}},
            "agent_result": {"metadata": {"praxile": {"source_result_sha256": source,
                "trajectory_task_id": arm, "experience_mode": native["experience_mode"]}}}}
        write(trial_root / "result.json", trial)
        write(trial_root / "verifier/reward.json", {"reward": 1})
        write(trial_root / "verifier/verification.json", {"status": "ok", "reward": 1})
        write(trial_root / "verifier/judge-details.json", {"total_score": 1, "max_score": 1, "total_count": 1,
            "private": "PRIVATE_RUBRIC", "rubrics": [{"index": 0, "weight": 1, "result": {"passed": True, "score": 1}}]})
        write(trial_root / "verifier/score.json", {"reward": 1, "test_status": "full_pass", "judges": [], "verdicts": []})
    write(root / "execution.json", {"schema_version": "praxile.native_pair_execution.v1",
        "plan_sha256": plan_hash, "status": "jobs_returned", "input_checks": [
            {"arm": arm, "phase": phase, "scope": "plan_and_local_inputs", "status": "unchanged"}
            for arm in ("baseline", "candidate") for phase in ("before", "after")]})
    monkeypatch.setattr(collector, "task_checksum", lambda path: "frozen")
    return root, tmp_path / "jobs/paired-candidate/trial"


def test_collects_official_scores_and_native_binding_without_private_details(case):
    root, _ = case
    report = collector.collect_pair(root)
    assert report["comparison"]["sample_count"] == 1
    assert report["comparison"]["delta"]["estimate"] == 0
    assert len(report["bindings"]) == 2
    assert "PRIVATE_RUBRIC" not in json.dumps(report)
    assert not report["promotion_eligible"] and not report["controlled_experiment_verified"]


def test_missing_trial_is_not_dropped(case):
    root, trial = case
    (trial / "result.json").unlink()
    report = collector.collect_pair(root)
    assert report["comparison"]["sample_count"] == 0
    assert report["comparison"]["incomplete_task_count"] == 1


def test_unbound_score_preserved_but_excluded_from_comparison(case):
    root, trial = case
    value = json.loads((trial / "result.json").read_text())
    value.pop("agent_result")
    write(trial / "result.json", value)
    report = collector.collect_pair(root)
    assert report["comparison"]["sample_count"] == 0
    assert report["official_outcomes"][1]["status"] == "completed"


@pytest.mark.parametrize("mutation", ["confounded", "missing_check", "wrong_plan"])
def test_rejects_unverified_execution(case, mutation):
    root, _ = case
    state = json.loads((root / "execution.json").read_text())
    if mutation == "confounded":
        state["status"] = "confounded"
    elif mutation == "missing_check":
        state["input_checks"].pop()
    else:
        state["plan_sha256"] = "different"
    write(root / "execution.json", state)
    with pytest.raises(ValueError):
        collector.collect_pair(root)


def test_rejects_different_candidate_even_with_consistent_trace(case):
    root, trial = case
    native = json.loads((trial / "agent/praxile-result.json").read_text())
    native["development_context"]["candidate_digest"] = "sha256:" + "a" * 64
    source = write(trial / "agent/praxile-result.json", native)
    write(trial / "agent/praxile-events.json", {"schema_version": "praxile.native_event_projection.v1",
        "source_result_sha256": source, "events": [e.to_dict() for e in native_run_to_events(native)]})
    record = json.loads((trial / "result.json").read_text())
    record["agent_result"]["metadata"]["praxile"]["source_result_sha256"] = source
    write(trial / "result.json", record)
    with pytest.raises(ValueError, match="different development candidate"):
        collector.collect_pair(root)


def test_cli_creates_private_report_without_overwriting(case, monkeypatch):
    root, _ = case
    output = root / "comparison.json"
    monkeypatch.setattr("sys.argv", ["results", "--pair-dir", str(root), "--output", str(output)])
    collector.main()
    assert output.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE_RUBRIC" not in output.read_text()
    with pytest.raises(FileExistsError):
        collector.main()
