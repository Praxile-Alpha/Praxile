import json

import pytest

from praxile.eval import five_arm_cli as cli
from praxile.eval.five_arm_execution import prepare_study
from test_five_arm_execution import setup


def inputs(tmp_path, monkeypatch):
    plan, _, task, options = setup(tmp_path, "frozen_executor")
    output = options.pop("directory")
    for name, value in (("plan", plan), ("task", task.to_dict()), ("execution", options)):
        (tmp_path / (name + ".json")).write_text(json.dumps(value, default=str))
    monkeypatch.setattr(cli, "check_worker_wheel", lambda *args: None)
    args = ["--plan", str(tmp_path / "plan.json"), "--task", str(tmp_path / "task.json"),
            "--execution-config", str(tmp_path / "execution.json"), "--output", str(output)]
    return args, output


def test_plan_only_is_readonly_and_does_not_dispatch(tmp_path, monkeypatch, capsys):
    args, output = inputs(tmp_path, monkeypatch)
    before = sorted(tmp_path.rglob("*"))
    def reject(*args, **kwargs):
        pytest.fail("preflight dispatched execution")
    monkeypatch.setattr(cli, "execute_study", reject)
    assert cli.main(args) == 0
    assert sorted(tmp_path.rglob("*")) == before
    assert not output.exists()
    text = capsys.readouterr().out
    report = json.loads(text.splitlines()[0])
    assert report["planned_cells"] == 5
    assert report["promotion_eligible"] is False
    assert report["execute"] is False
    assert "PRIVATE" not in text and str(tmp_path) not in text


@pytest.mark.parametrize("status,code", [("dispatched_not_scored", 0), ("failed", 1)])
def test_execute_requires_optin_and_forwards_options(tmp_path, monkeypatch, capsys, status, code):
    args, output = inputs(tmp_path, monkeypatch)
    async def run(plan, tasks, **kwargs):
        assert len(tasks) == 1
        assert kwargs["execution"]["credential_file"] == tmp_path / "key"
        assert kwargs["execution"]["model_request_options"] == {"reasoning_effort": "low"}
        assert kwargs["judge"] is None
        output.mkdir()
        path = output / "study.json"
        path.write_text(json.dumps({"status": status}))
        return path
    monkeypatch.setattr(cli, "execute_study", run)
    config = tmp_path / "execution.json"
    options = json.loads(config.read_text())
    options["model_request_options"] = {"reasoning_effort": "low"}
    config.write_text(json.dumps(options))
    assert cli.main(args + ["--execute"]) == code
    assert "not task success" in capsys.readouterr().out


@pytest.mark.parametrize("defect", ["duplicate_task", "unknown_option", "duplicate_key", "nonfinite", "existing",
                                    "model", "endpoint", "raw_key", "missing_wheel", "bad_root"])
def test_invalid_config_fails_without_state_or_private_output(tmp_path, monkeypatch, capsys, defect):
    args, output = inputs(tmp_path, monkeypatch)
    path = tmp_path / "execution.json"
    options = json.loads(path.read_text())
    if defect == "duplicate_task":
        args += ["--task", str(tmp_path / "task.json")]
    elif defect == "duplicate_key":
        path.write_text('{"PRIVATE": 1, "PRIVATE": 2}')
    elif defect == "nonfinite":
        path.write_text('{"PRIVATE": NaN}')
    elif defect == "existing":
        output.mkdir()
    elif defect == "bad_root":
        roots = tmp_path / "roots.json"
        roots.write_text('{"task": null}')
        args += ["--input-roots", str(roots)]
    elif defect == "missing_wheel":
        def missing(*args):
            raise ValueError("PRIVATE_WHEEL_ERROR")
        monkeypatch.setattr(cli, "check_worker_wheel", missing)
    else:
        key, value = {"unknown_option": ("per_arm", "PRIVATE"), "model": ("model", 3),
                      "endpoint": ("base_url", "https://PRIVATE@host.invalid"),
                      "raw_key": ("api_key", "PRIVATE_KEY")}[defect]
        options[key] = value
        path.write_text(json.dumps(options))
    before = sorted(tmp_path.rglob("*"))
    assert cli.main(args) == 2
    assert sorted(tmp_path.rglob("*")) == before
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err


def test_interruption_is_not_success(tmp_path, monkeypatch, capsys):
    args, _ = inputs(tmp_path, monkeypatch)
    async def run(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "execute_study", run)
    assert cli.main(args + ["--execute"]) == 130
    assert "No automatic replay" in capsys.readouterr().err


def test_preflight_snapshots_mutable_arguments(tmp_path):
    plan, _, task, options = setup(tmp_path, "frozen_executor")
    root = options.pop("directory")
    prepared = prepare_study(plan, {task.task_id: task}, directory=root, execution=options)
    options["model"] = "changed"
    plan["spec"]["task_ids"].append("changed")
    assert prepared["options"]["model"] == "fixture"
    assert prepared["plan"]["spec"]["task_ids"] == ["task"]
    assert not root.exists()


@pytest.mark.parametrize("tamper", [False, True])
def test_independent_judge_configuration_is_frozen_and_forwarded(tmp_path, monkeypatch, capsys, tamper):
    from test_five_arm_study_judge import fixture
    task, plan, execution, judge, _ = fixture(tmp_path)
    output = tmp_path / "study"
    if tamper:
        judge["model"] = "PRIVATE_CHANGED_MODEL"
    for name, value in (("plan", plan), ("task", task.to_dict()), ("execution", execution),
                        ("judge", judge), ("roots", {"task": str(tmp_path / "public-inputs")})):
        (tmp_path / (name + ".json")).write_text(json.dumps(value, default=str))
    args = ["--plan", str(tmp_path / "plan.json"), "--task", str(tmp_path / "task.json"),
            "--execution-config", str(tmp_path / "execution.json"), "--judge-config", str(tmp_path / "judge.json"),
            "--input-roots", str(tmp_path / "roots.json"), "--output", str(output), "--execute"]
    monkeypatch.setattr(cli, "check_worker_wheel", lambda *args: None)
    calls = []
    async def run(plan, tasks, **kwargs):
        calls.append(kwargs)
        assert kwargs["judge"] == judge
        assert kwargs["input_source_roots"] == {"task": tmp_path / "public-inputs"}
        output.mkdir()
        path = output / "study.json"
        path.write_text('{"status": "dispatched_not_scored"}')
        return path
    monkeypatch.setattr(cli, "execute_study", run)
    assert cli.main(args) == (2 if tamper else 0)
    assert len(calls) == (0 if tamper else 1)
    assert output.exists() is not tamper
    text = capsys.readouterr()
    assert "PRIVATE" not in text.out + text.err
    assert "fixture-secret" not in text.out + text.err
