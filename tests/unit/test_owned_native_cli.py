import hashlib
import json
import zipfile

import pytest

from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference
from praxile.eval.owned_native_cli import load_task, main
from praxile.eval.worker_preflight import check_worker_wheel


def setup_args(tmp_path):
    wheel = tmp_path / "praxile.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("praxile/eval/native_worker.py", "# fixture")
        archive.writestr("praxile/eval/process_supervisor.py", "# fixture")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    task = BenchmarkTask(task_id="cli-fixture", benchmark="fixture", dataset_revision="1",
        split="development", instruction="PRIVATE_TASK_BODY",
        environment=EnvironmentSpec("harbor", "PRIVATE_SOURCE", "python@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("judge", "1", "PRIVATE_EVALUATOR", "sha256:" + "f" * 64))
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task.to_dict()))
    return ["--task", str(task_path), "--state-root", str(tmp_path / "runs"),
        "--wheel", str(wheel), "--wheel-sha256", digest,
        "--model", "fixture", "--base-url", "https://provider.invalid/v1"]


def test_plan_does_not_run_or_print_private_fields(tmp_path, monkeypatch, capsys):
    from praxile.eval import owned_native_run
    def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")
    monkeypatch.setattr(owned_native_run, "run_owned_native", forbidden)
    assert main(setup_args(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "Plan only" in output and "PRIVATE_" not in output
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_task_local_policy_plan_and_execution(tmp_path, monkeypatch, capsys, enabled):
    from praxile.eval import owned_native_run
    args = setup_args(tmp_path) + (["--task-local-memory"] if enabled else [])
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out.splitlines()[0])
    assert plan["task_local_memory_enabled"] is enabled
    async def run(task, **kwargs):
        assert kwargs["task_local_memory_enabled"] is enabled
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"cleanup_verified": True, "required_outputs_collected": True}))
        return report
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    assert main([*args, "--execute"]) == 0


@pytest.mark.parametrize("value", [None, 0, 1, "false", {}])
def test_task_local_policy_api_rejects_non_boolean_before_resources(tmp_path, value):
    import asyncio
    from praxile.eval.owned_native_run import run_owned_native
    with pytest.raises(ValueError, match="must be boolean"):
        asyncio.run(run_owned_native(None, state_root=tmp_path, image="unused", wheel=tmp_path / "none",
            wheel_sha256="a" * 64, model="fixture", base_url="unused", task_local_memory_enabled=value))
    assert not list(tmp_path.iterdir())


def test_worker_requirements_plan_and_execution(tmp_path, monkeypatch, capsys):
    from praxile.eval import owned_native_run
    dependency = tmp_path / "dep-1-py3-none-any.whl"
    dependency.write_bytes(b"fixture")
    digest = hashlib.sha256(dependency.read_bytes()).hexdigest()
    args = [*setup_args(tmp_path), "--dependency-wheel", str(dependency), digest,
            "--require-capability", "xlsx_values.v1"]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out.splitlines()[0])
    assert plan["worker_requirements"] == {"dependencies": [{"filename": dependency.name, "sha256": digest}],
                                           "required_capabilities": ["xlsx_values.v1"]}
    calls = []
    async def run(task, **kwargs):
        calls.append(kwargs)
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"cleanup_verified": True, "required_outputs_collected": True}))
        return report
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    assert main([*args, "--execute"]) == 0
    assert calls[0]["required_capabilities"] == ["xlsx_values.v1"]
    assert calls[0]["dependency_wheels"] == [{"path": str(dependency.resolve()), "sha256": digest}]
    dependency.write_bytes(b"changed")
    assert main([*args, "--execute"]) == 2
    assert len(calls) == 1


@pytest.mark.parametrize("profiles", [["unknown"], ["xlsx_values.v1"] * 2])
def test_invalid_worker_profile_rejected_without_running(tmp_path, profiles):
    args = setup_args(tmp_path)
    for profile in profiles:
        args += ["--require-capability", profile]
    assert main(args) == 2
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("complete, expected", [(True, 0), (False, 1)])
def test_execute_is_explicit_and_reports_incomplete_outputs(tmp_path, monkeypatch, complete, expected):
    from praxile.eval import owned_native_run
    calls = []
    async def run(task, **kwargs):
        calls.append((task, kwargs))
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"cleanup_verified": True, "required_outputs_collected": complete}))
        return report
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    assert main([*setup_args(tmp_path), "--execute"]) == expected
    assert len(calls) == 1
    assert calls[0][1]["max_steps"] == 50


def test_invalid_endpoint_is_redacted(tmp_path, capsys):
    args = setup_args(tmp_path)
    args[-1] = "https://user:SECRET_TOKEN@example.com/v1"
    assert main(args) == 2
    captured = capsys.readouterr()
    assert "SECRET_TOKEN" not in captured.out + captured.err
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("body", ['{"task_id":"a","task_id":"b"}', '{"value":NaN}'])
def test_task_json_is_strict(tmp_path, body):
    path = tmp_path / "task.json"
    path.write_text(body)
    with pytest.raises(ValueError):
        load_task(path)


def test_old_worker_rejected_before_execution(tmp_path):
    wheel = tmp_path / "old.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("praxile/__init__.py", "")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="lacks native worker"):
        check_worker_wheel(wheel, digest)
    with pytest.raises(ValueError, match="digest mismatch"):
        check_worker_wheel(wheel, "a" * 64)


def test_interruption_returns_130(tmp_path, monkeypatch):
    from praxile.eval import owned_native_run
    async def run(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    assert main([*setup_args(tmp_path), "--execute"]) == 130


def test_persistent_workspace_is_explicit(tmp_path, monkeypatch):
    from praxile.eval import owned_native_run
    calls = []
    async def run(task, **kwargs):
        calls.append(kwargs)
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"cleanup_verified": True, "required_outputs_collected": True}))
        return report
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    args = setup_args(tmp_path)
    assert main([*args, "--execute", "--persist-workspace"]) == 0
    assert calls[0]["persist_workspace"] is True


def test_shared_quota_plan_is_read_only_and_execute_forwards_same_database(tmp_path, monkeypatch, capsys):
    from praxile.eval import owned_native_run
    from praxile.eval.persistent_chat_budget import PersistentChatBudget
    path = tmp_path / "budget.sqlite"
    budget = PersistentChatBudget.create(path, max_calls=2, output_budget=100, ttl_seconds=60)
    args = [*setup_args(tmp_path), "--credential-file", str(tmp_path / "key"), "--shared-budget-db", str(path)]
    before = budget.status()
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out.splitlines()[0])
    assert plan["shared_chat_budget"] == before
    assert budget.status() == before
    assert not (tmp_path / "runs").exists()
    async def run(task, **kwargs):
        kwargs["shared_chat_budget"].reserve("executor", 20)
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"cleanup_verified": True, "required_outputs_collected": True}))
        return report
    monkeypatch.setattr(owned_native_run, "run_owned_native", run)
    assert main([*args, "--execute"]) == 0
    assert budget.status()["remaining_calls"] == 1
    assert budget.status()["remaining_output"] == 80


@pytest.mark.parametrize("defect", ["missing", "corrupt", "no_credential", "resume", "exhausted"])
def test_unavailable_shared_quota_fails_before_execution(tmp_path, monkeypatch, defect):
    from praxile.eval import owned_native_run
    from praxile.eval.persistent_chat_budget import PersistentChatBudget
    path = tmp_path / "budget.sqlite"
    if defect == "corrupt":
        path.write_text("not a database")
    elif defect != "missing":
        budget = PersistentChatBudget.create(path, max_calls=1, output_budget=10, ttl_seconds=60)
        if defect == "exhausted":
            budget.reserve("executor", 10)
    args = [*setup_args(tmp_path), "--shared-budget-db", str(path), "--execute"]
    if defect != "no_credential":
        args += ["--credential-file", str(tmp_path / "key")]
    if defect == "resume":
        args += ["--resume", "--run-id", "fixture"]
    def forbidden(*args, **kwargs):
        raise AssertionError("must not execute")
    monkeypatch.setattr(owned_native_run, "run_owned_native", forbidden)
    assert main(args) == 2
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("value", ["true", 1, None])
def test_persistent_workspace_requires_boolean_before_optional_dependencies(tmp_path, value):
    from praxile.eval.harbor_lease import HarborDockerLease
    with pytest.raises(ValueError, match="must be boolean"):
        HarborDockerLease(tmp_path, image="unused", persist_workspace=value)
