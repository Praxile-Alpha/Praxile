"""Run with the pinned official jobbench_harbor package on PYTHONPATH."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

prepare = pytest.importorskip("jobbench_harbor.prepare")
pytest.importorskip("harbor")
from harbor.models.job.config import JobConfig
from praxile.eval.jobbench import JobBenchGeneration, main

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_real_upstream_generation_and_native_job_schema(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    for split in ("dataset", "dataset_easy"):
        task = snapshot / split / "writer/task1"
        (task / "task_folder").mkdir(parents=True)
        (task / "task_folder/TASK_INSTRUCTIONS.txt").write_text("Write a report using the reference.")
        (task / "task_folder/reference.txt").write_text("Public reference")
        (task / "RUBRICS.json").write_text(json.dumps({"rubrics": [
            {"rubric": "PRIVATE_RUBRIC", "criterion": ["Correct"], "weight": 1}
        ]}))
        (task / "task_card.md").write_text("Private task card")
    upstream_root = Path(prepare.__file__).resolve().parents[2]
    root = tmp_path / "generation"
    manifest = prepare.generate(snapshot, root, repo_id="JobBench/job-bench", revision="a" * 40,
                                judge_path=upstream_root / "eval/judge.py")
    current = (root / "current").resolve(strict=True)
    digest = hashlib.sha256((current / "manifest.json").read_bytes()).hexdigest()
    generation = JobBenchGeneration(current, manifest_sha256=digest)
    assert len(generation.tasks) == manifest["task_count"] == 2
    config = generation.job_config(
        agent={"import_path": "praxile.adapters.harbor_native:PraxileAgent", "model_name": "fixture",
               "kwargs": {"wheel_path": "/not-executed/fixture.whl", "wheel_sha256": "b" * 64,
                          "workspace": "/workspace", "base_url": "http://localhost:18888/v1"}},
        jobs_dir=tmp_path / "jobs", job_name="upstream-contract", split="easy")
    parsed = JobConfig.model_validate(config)
    assert len(parsed.tasks) == 1
    agent_path = tmp_path / "agent.json"
    agent_path.write_text(json.dumps(config["agents"][0]))
    job_path = tmp_path / "job.json"
    monkeypatch.setattr("sys.argv", ["jobbench", "--generation", str(current),
        "--manifest-sha256", digest, "--split", "easy", "--agent-config", str(agent_path),
        "--job-name", "upstream-contract", "--jobs-dir", str(tmp_path / "jobs"), "--write-job", str(job_path)])
    main()
    assert json.loads(job_path.read_text()) == config
    assert job_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        main()
    from dirhash import dirhash
    from praxile.eval.jobbench_results import main as import_main
    selected = generation.select(split="easy")[0]
    trial_dir = tmp_path / "interrupted-trial"
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(json.dumps({"task_name": "jobbench/" + selected.task_id,
        "task_checksum": dirhash(generation.paths[selected.task_id], "sha256")}))
    report_path = tmp_path / "imported.json"
    monkeypatch.setattr("sys.argv", ["import", "--generation", str(current), "--manifest-sha256", digest,
        "--task-id", selected.task_id, "--trial", str(trial_dir), "--output", str(report_path)])
    import_main()
    assert json.loads(report_path.read_text())["outcome"]["status"] == "infra_error"
    assert report_path.stat().st_mode & 0o777 == 0o600
    for task in generation.tasks.values():
        assert all("tests/" not in item.path for item in task.inputs)
        assert "PRIVATE_RUBRIC" not in task.instruction


def test_official_empty_deliverable_verification_and_import(tmp_path):
    pytest.importorskip("openpyxl")
    from praxile.eval.jobbench_results import import_trial
    upstream = Path(prepare.__file__).resolve().parents[2]
    rubric = tmp_path / "RUBRICS.json"
    rubric.write_text(json.dumps({"rubrics": [
        {"rubric": "Deliver a report", "criterion": ["Exists"], "weight": 3}
    ]}))
    trial_dir = tmp_path / "trial"
    completed = subprocess.run([
        sys.executable, str(upstream / "harbor/runtime/verify.py"),
        "--output-dir", str(tmp_path / "empty-output"), "--rubrics-file", str(rubric),
        "--judge-path", str(upstream / "eval/judge.py"), "--logs-dir", str(trial_dir / "verifier"),
    ], env={**os.environ, "JUDGE_API_BASE": "http://127.0.0.1:9/v1", "JUDGE_API_KEY": ""},
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    # Synthetic trial identity around real official verifier output, not an agent run.
    (trial_dir / "result.json").write_text(json.dumps({
        "task_name": "jobbench/fixture", "task_checksum": "a" * 64, "finished_at": "2026-10-19T00:00:00Z",
        "verifier_environment_mode": "separate", "verifier_result": {"rewards": {"reward": 0}},
    }))
    report = import_trial("fixture", trial_dir, expected_task_checksum="a" * 64, expected_rubric_weights=(3,))
    assert report["outcome"]["status"] == "completed"
    assert report["outcome"]["scores"][0]["value"] == 0
    assert report["outcome"]["scores"][0]["maximum"] == 3
    assert report["execution_status"] == "unknown"
