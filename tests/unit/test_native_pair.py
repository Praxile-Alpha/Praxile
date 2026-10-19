from copy import deepcopy
import hashlib
import json

import pytest

from praxile.eval.native_pair import digest, main, prepare_pair
from praxile.eval.v2.candidate import ContextCandidate


def candidate():
    return ContextCandidate(candidate_id="pair", version="1", title="Focused check",
        candidate_type="context_policy", context_item={"content": "Check document page count."},
        confidence=0, evidence_refs=("event:source",), expected_effect={"hypothesis": "Avoid invalid output"},
        applies_to={"repositories": ["docs"], "task_signals": ["one-page"],
                    "does_not_apply_when": ["multi-page"]}).to_dict()


def job(tmp_path):
    return {"job_name": "paired", "jobs_dir": str(tmp_path / "jobs"),
        "tasks": [{"path": str(tmp_path / "task")}], "datasets": [],
        "verifier": {"disable": False},
        "agents": [{"import_path": "praxile.adapters.harbor_native:PraxileAgent", "model_name": "fixture",
                    "kwargs": {"max_steps": 20, "timeout_seconds": 60, "wheel_sha256": "a" * 64}}]}


def test_pair_only_varies_candidate_and_job_identity(tmp_path):
    base = job(tmp_path)
    original = deepcopy(base)
    plan, arms = prepare_pair(base, candidate(), repository="docs", output_dir=tmp_path / "pair")
    assert base == original
    assert not plan["execution_started"] and not plan["promotion_eligible"]
    assert not plan["controlled_experiment_verified"]
    for arm in arms:
        assert digest(arms[arm]) == plan["arms"][arm]["config_sha256"]
        assert arms[arm]["n_concurrent_trials"] == 1
        assert arms[arm]["n_attempts"] == 1
        assert arms[arm]["retry"] == {"max_retries": 0}
    baseline, changed = arms["baseline"], arms["candidate"]
    changed["job_name"] = baseline["job_name"]
    for key in ("development_candidate_file", "development_candidate_sha256", "development_only", "repository"):
        changed["agents"][0]["kwargs"].pop(key)
    assert changed == baseline


@pytest.mark.parametrize("mutation", ["override", "budget", "datasets", "multi_agent", "existing", "regrade"])
def test_rejects_ambiguous_or_existing_runs(tmp_path, mutation):
    base = job(tmp_path)
    if mutation == "override":
        base["agents"][0]["kwargs"]["development_only"] = False
    elif mutation == "budget":
        base["agents"][0]["kwargs"]["max_steps"] = True
    elif mutation == "datasets":
        base["datasets"] = [{"name": "mutable"}]
    elif mutation == "multi_agent":
        base["agents"] *= 2
    elif mutation == "existing":
        (tmp_path / "jobs/paired-candidate").mkdir(parents=True)
    else:
        base["source_jobs"] = [{"path": "old"}]
    with pytest.raises(ValueError):
        prepare_pair(base, candidate(), repository="docs", output_dir=tmp_path / "pair")


@pytest.mark.parametrize("freeze", [False, True])
def test_cli_freezes_candidate_and_refuses_overwrite(tmp_path, monkeypatch, freeze):
    source, config, output = tmp_path / "source.json", tmp_path / "job.json", tmp_path / "pair"
    source.write_text(json.dumps(candidate()))
    base = job(tmp_path)
    if freeze:
        (tmp_path / "task").mkdir()
        (tmp_path / "task/instruction.md").write_text("public task")
        wheel = tmp_path / "worker.whl"
        wheel.write_bytes(b"fixture")
        base["agents"][0]["kwargs"].update(wheel_path=str(wheel), wheel_sha256=hashlib.sha256(b"fixture").hexdigest())
    config.write_text(json.dumps(base))
    monkeypatch.setattr("sys.argv", ["native_pair", "--job", str(config), "--candidate", str(source),
        "--candidate-sha256", hashlib.sha256(source.read_bytes()).hexdigest(),
        "--repository", "docs", "--output-dir", str(output), *(["--freeze-local-inputs"] if freeze else [])])
    main()
    plan = json.loads((output / "manifest.json").read_bytes())
    assert ("local_inputs" in plan) is freeze
    assert hashlib.sha256((output / "candidate-asset.json").read_bytes()).hexdigest() == plan["candidate_file_sha256"]
    assert (output.stat().st_mode & 0o777) == 0o700
    assert (output / "manifest.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        main()
    source.write_text("{}")
    assert json.loads((output / "candidate-asset.json").read_bytes()) == candidate()
