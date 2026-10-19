import json
from dataclasses import asdict

import pytest

from praxile.eval.workbuddy import WorkBuddyDataset, prepare_copy
from praxile.eval.v2.schema import EvalSchemaError


@pytest.fixture
def package(tmp_path):
    files = {
        "dataset.toml": '[dataset]\nid="wb-bench-office-v1.0"\nschema="workbuddy.dataset.v1"\ntask_count=1\n[layout]\ntask_root="tasks"\n[verifier]\nschema="workbuddy.verifier.v1"\nengine="composite"\n',
        "shared/verifier/plugin.py": "# private official plugin\n",
        "tasks/example/task.toml": '[task]\nname="workbuddy/example"\n[agent]\ntimeout_sec=60\n[verifier]\ntimeout_sec=60\n',
        "tasks/example/instruction.md": "Create the requested document.",
        "tasks/example/environment/workspace.tar.gz": "opaque nested archive",
        "tasks/example/tests/rubric.json": '{"private":"hidden answer"}',
        "tasks/example/tests/test.sh": "#!/bin/sh\nexit 1\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def load(root, **kwargs):
    return WorkBuddyDataset(root, revision="a" * 40, **kwargs)


def test_readiness_and_private_projection(package):
    dataset = load(package)
    assert dataset.preparation_reasons == {"example": [
        "bind_official_composite_verifier", "configure_nonroot_task_users"]}
    task, = dataset.select(("example",))
    view = json.dumps(asdict(task.to_adapter_task("/workspace", trace_id="t", run_id="r")))
    for secret in ("hidden answer", "rubric", "tests", str(package)):
        assert secret not in view
    config = package / "tasks/example/task.toml"
    config.write_text(config.read_text().replace('[agent]', '[agent]\nuser="dev"').replace(
        '[verifier]', '[verifier]\nuser="dev"\nimport_path="workbuddy_bench.judge:CompositeVerifier"'))
    assert load(package).preparation_required == []


def test_inventory_freezes_private_materials(package):
    before = load(package)
    assert load(package, expected_digest=before.digest).digest == before.digest
    (package / "tasks/example/tests/rubric.json").write_text("changed")
    with pytest.raises(EvalSchemaError, match="digest mismatch"):
        load(package, expected_digest=before.digest)
    assert load(package).tasks["example"].evaluator != before.tasks["example"].evaluator


@pytest.mark.parametrize("ids", [("missing",), ("example", "example")])
def test_selection_rejects_unknown_and_duplicate_ids(package, ids):
    with pytest.raises(EvalSchemaError, match="unknown or duplicate"):
        load(package).select(ids)


def test_rejects_unapproved_verifier(package):
    config = package / "tasks/example/task.toml"
    config.write_text(config.read_text() + 'import_path="fake:Verifier"\n')
    with pytest.raises(EvalSchemaError, match="CompositeVerifier"):
        load(package)


def test_rejects_symlink_and_mutable_revision(package):
    with pytest.raises(EvalSchemaError, match="immutable"):
        WorkBuddyDataset(package, revision="main")
    (package / "link").symlink_to(package / "dataset.toml")
    with pytest.raises(EvalSchemaError):
        load(package)


def test_official_preparation_on_copy(package, tmp_path_factory):
    pytest.importorskip("workbuddy_bench.runner.prepare_tasks")
    original = load(package)
    destination = tmp_path_factory.mktemp("prepared") / "dataset"
    prepared = prepare_copy(package, destination, revision="a" * 40, expected_digest=original.digest)
    assert prepared.preparation_required == []
    assert load(package).digest == original.digest
    record = json.loads((destination / "praxile-preparation.json").read_text())
    assert record["source_inventory_sha256"] == original.digest
    assert record["task_users_changed"] == 1
    assert record["verifier_bindings_changed"] == 1
    assert len(record["helper_sha256"]) == 64
    with pytest.raises(FileExistsError):
        prepare_copy(package, destination, revision="a" * 40, expected_digest=original.digest)
    with pytest.raises(EvalSchemaError, match="outside"):
        prepare_copy(package, package / "nested", revision="a" * 40, expected_digest=original.digest)


def test_preparation_failure_does_not_publish_or_modify_source(package, tmp_path_factory, monkeypatch):
    helpers = pytest.importorskip("workbuddy_bench.runner.prepare_tasks")
    original = load(package)
    parent = tmp_path_factory.mktemp("failure")

    def fail(path, **kwargs):
        (path / "example/task.toml").write_text("partial modification")
        raise OSError("simulated preparation failure")

    monkeypatch.setattr(helpers, "ensure_task_user", fail)
    with pytest.raises(OSError, match="simulated"):
        prepare_copy(package, parent / "dataset", revision="a" * 40, expected_digest=original.digest)
    assert not (parent / "dataset").exists()
    assert not list(parent.glob(".workbuddy-prepare-*"))
    assert load(package).digest == original.digest


@pytest.fixture
def prepared_package(package):
    config = package / "tasks/example/task.toml"
    config.write_text(config.read_text().replace('[agent]', '[agent]\nuser="dev"').replace(
        '[verifier]', '[verifier]\nuser="dev"\nimport_path="workbuddy_bench.judge:CompositeVerifier"'))
    return package


def native_agent():
    return {"import_path": "praxile.adapters.harbor_native:WorkBuddyPraxileAgent",
            "model_name": "configurable-model", "kwargs": {"workspace": "/workspace"}}


def test_job_preserves_official_contract_and_bounded_lifecycle(prepared_package, tmp_path):
    dataset = load(prepared_package)
    agent = native_agent()
    job = dataset.job_config(agent=agent, jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))
    assert job["n_concurrent_trials"] == job["n_attempts"] == 1
    assert job["retry"]["max_retries"] == 0
    assert job["environment"]["delete"] is True
    assert job["environment"]["import_path"].startswith("workbuddy_bench.")
    assert job["tasks"] == [{"path": str(prepared_package / "tasks/example")}]
    assert job["verifier"] == {"import_path": "workbuddy_bench.judge:CompositeVerifier"}
    agent["kwargs"]["workspace"] = "/elsewhere"
    assert job["agents"][0]["kwargs"]["workspace"] == "/workspace"


def test_job_refuses_unprepared_tasks(package, tmp_path):
    with pytest.raises(EvalSchemaError, match="preparation"):
        load(package).job_config(agent=native_agent(), jobs_dir=tmp_path / "jobs",
                                 job_name="smoke", task_ids=("example",))


def test_job_collects_explicit_output_before_container_deletion(prepared_package, tmp_path):
    dataset = load(prepared_package)
    agent = native_agent()
    agent["kwargs"]["output_directory"] = "output/reports"
    job = dataset.job_config(agent=agent, jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))
    assert job["artifacts"] == [{"source": "/workspace/output/reports", "exclude": ["__pycache__", "*.pyc"]}]
    del agent["kwargs"]["output_directory"]
    assert dataset.job_config(agent=agent, jobs_dir=tmp_path / "jobs", job_name="smoke",
                              task_ids=("example",))["artifacts"] == []


@pytest.mark.parametrize("output", ["", ".", "/", "../tests", "output/../../tests", "output\\other", 42, "out\nput"])
def test_job_rejects_unsafe_output_collection(prepared_package, tmp_path, output):
    agent = native_agent()
    agent["kwargs"]["output_directory"] = output
    with pytest.raises(EvalSchemaError, match="output_directory"):
        load(prepared_package).job_config(agent=agent, jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))


def test_job_revalidates_source_and_refuses_security(prepared_package, tmp_path):
    dataset = load(prepared_package)
    config = prepared_package / "dataset.toml"
    config.write_text(config.read_text().replace("office", "sec"))
    options = dict(agent=native_agent(), jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))
    with pytest.raises(EvalSchemaError, match="digest"):
        dataset.job_config(**options)
    with pytest.raises(EvalSchemaError, match="Security"):
        load(prepared_package).job_config(**options)


@pytest.mark.parametrize("override,match", [
    ({"task_ids": ()}, "explicit task IDs"),
    ({"job_name": "../escape"}, "unsafe"),
    ({"agent": {}}, "import_path"),
    ({"agent": {**native_agent(), "name": "oracle"}}, "conflicting"),
    ({"agent": {**native_agent(), "model_name": None}}, "model_name"),
    ({"agent": {**native_agent(), "kwargs": None}}, "workspace"),
])
def test_job_rejects_invalid_execution_config(prepared_package, tmp_path, override, match):
    options = dict(agent=native_agent(), jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))
    options.update(override)
    with pytest.raises(EvalSchemaError, match=match):
        load(prepared_package).job_config(**options)


def test_job_schema_and_existing_run(prepared_package, tmp_path):
    pytest.importorskip("harbor")
    from harbor.models.job.config import JobConfig
    dataset = load(prepared_package)
    options = dict(agent=native_agent(), jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",))
    job = JobConfig.model_validate(dataset.job_config(**options))
    assert job.retry.max_retries == 0
    assert job.environment.delete is True
    assert job.verifier.import_path == "workbuddy_bench.judge:CompositeVerifier"
    (tmp_path / "jobs/smoke").mkdir(parents=True)
    with pytest.raises(EvalSchemaError, match="already exists"):
        dataset.job_config(**options)


def test_harbor_factory_selects_official_verifier(prepared_package, tmp_path):
    pytest.importorskip("harbor")
    pytest.importorskip("workbuddy_bench.judge")
    from harbor.models.job.config import JobConfig
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import TrialPaths
    from harbor.verifier.factory import VerifierFactory
    from workbuddy_bench.judge import CompositeVerifier
    job = JobConfig.model_validate(load(prepared_package).job_config(
        agent=native_agent(), jobs_dir=tmp_path / "jobs", job_name="smoke", task_ids=("example",)))
    task = Task(job.tasks[0].path)
    verifier = VerifierFactory.create_verifier_from_config(
        job.verifier, task=task, trial_paths=TrialPaths(tmp_path / "trial"), environment=None)
    assert type(verifier) is CompositeVerifier
    assert task.config.agent.user == task.config.verifier.user == "dev"


def test_cli_writes_exclusive_private_job(prepared_package, tmp_path, monkeypatch, capsys):
    pytest.importorskip("harbor")
    from importlib.metadata import version
    if version("harbor") != "0.18.0":
        pytest.skip("WorkBuddy requires pinned Harbor 0.18.0")
    from praxile.eval.workbuddy import main
    agent_path = tmp_path.parent / (tmp_path.name + "-agent.json")
    agent_path.write_text(json.dumps(native_agent()))
    output = tmp_path.parent / (tmp_path.name + "-job.json")
    monkeypatch.setattr("sys.argv", ["workbuddy", "--dataset", str(prepared_package),
        "--revision", "a" * 40, "--inventory-sha256", load(prepared_package).digest,
        "--task-id", "example", "--agent-config", str(agent_path), "--job-name", "smoke",
        "--jobs-dir", str(tmp_path.parent / "jobs"), "--write-job", str(output)])
    main()
    assert json.loads(capsys.readouterr().out)["selected_tasks"] == 1
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["verifier"]["import_path"].endswith(":CompositeVerifier")
    with pytest.raises(FileExistsError):
        main()
