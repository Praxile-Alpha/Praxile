import hashlib

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.eval.native_worker import load_task_input
from praxile.eval.native_worker import benchmark_config
from praxile.eval.native_worker import run_native
from praxile.task_analyzer import TaskAnalyzer
from praxile.task_input import TaskInput


def test_explicit_intent_preserves_environment_without_false_architecture_gate(tmp_path):
    analyzer = TaskAnalyzer(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    task = TaskInput("Create an exhibition award memo.",
                     "If the task asks to modify a database, save a copy in output.", "task.txt")
    assert analyzer.analyze(task.prompt())["architecture_gate_required"]
    result = analyzer.analyze(task.body, environment_instructions=task.environment_instructions)
    assert not result["architecture_gate_required"]
    assert task.environment_instructions in task.prompt()
    assert task.record()["body_sha256"] == hashlib.sha256(task.body.encode()).hexdigest()


def test_real_architecture_and_frozen_boundary_still_stop(tmp_path):
    analyzer = TaskAnalyzer(Config(default_config(tmp_path), ProjectPaths(tmp_path)))
    assert analyzer.analyze("Migrate the shared database schema.", environment_instructions="Save outputs.")["architecture_gate_required"]
    assert analyzer.analyze("Create a memo.", [{"kind": "rule", "path": "rules/frozen-boundaries/test.md"}])["architecture_gate_required"]
    assert analyzer.analyze("Create a memo.", environment_instructions="Contains private key.")["privacy_sensitive"]


def test_public_body_requires_matching_digest_and_no_symlink(tmp_path):
    body = tmp_path / "task.txt"
    body.write_text("Create a memo.")
    digest = hashlib.sha256(body.read_bytes()).hexdigest()
    assert load_task_input(tmp_path, "Read task.txt", "task.txt", digest).body == "Create a memo."
    with pytest.raises(ValueError, match="digest"):
        load_task_input(tmp_path, "Read task.txt", "task.txt", "0" * 64)
    link = tmp_path / "link.txt"
    link.symlink_to(body)
    with pytest.raises(ValueError, match="symlink"):
        load_task_input(tmp_path, "Read task.txt", "link.txt", digest)
    for path in (str(body), "../task.txt"):
        with pytest.raises(ValueError, match="relative"):
            load_task_input(tmp_path, "Read task.txt", path, digest)


def test_public_body_size_is_bounded(tmp_path):
    body = tmp_path / "task.txt"
    raw = b"x" * 262145
    body.write_bytes(raw)
    with pytest.raises(ValueError, match="size"):
        load_task_input(tmp_path, "Read task.txt", "task.txt", hashlib.sha256(raw).hexdigest())


def test_benchmark_request_timeout_is_explicit_and_bounded(tmp_path):
    config = benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=3,
                              model_timeout_seconds=90)
    assert config.get("runtime", "model_timeout_seconds") == 90
    for invalid in (True, 0, 111, "90"):
        with pytest.raises(ValueError, match="model_timeout_seconds"):
            benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=3,
                             model_timeout_seconds=invalid)


@pytest.mark.parametrize("directory", ["/tmp/out", "../out", ".", ""])
def test_output_directory_rejected_before_runtime(tmp_path, directory):
    with pytest.raises(ValueError, match="output directory"):
        run_native(tmp_path, "Create memo", model="fixture", base_url="http://localhost/v1", max_steps=1,
                   output_directory=directory)
    assert not (tmp_path / ".praxile").exists()
