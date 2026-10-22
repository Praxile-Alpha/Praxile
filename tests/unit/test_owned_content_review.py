from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.config import Config
from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
from praxile.eval.owned_content_review import prepare_owned_review, review_owned_output


def sha(data):
    return hashlib.sha256(data).hexdigest()


def setup(tmp_path, *, media_type="text/plain"):
    execution = tmp_path / "execution"
    evaluator = tmp_path / "private"
    evaluator.mkdir()
    for name in ("inputs", "outputs", "agent"):
        (execution / name).mkdir(parents=True)
    source, output = b"retain the source", b"draft output"
    (execution / "inputs/source.txt").write_bytes(source)
    (execution / "outputs/output.txt").write_bytes(output)
    trajectory = json.dumps({"schema_version": "praxile.native_benchmark_run.v1", "trajectory": {}}).encode()
    (execution / "agent/praxile-result.json").write_bytes(trajectory)
    rubric = json.dumps({"requirements": [{"id": "r", "requirement": "PRIVATE_JUDGE_REQUIREMENT",
        "source_quote": "retain the source"}]}).encode()
    (evaluator / "rubric.json").write_bytes(rubric)
    task = BenchmarkTask(task_id="review-fixture", benchmark="fixture", dataset_revision="1", split="development",
        instruction="public task", environment=EnvironmentSpec("harbor", "fixture", "image@sha256:" + "a" * 64),
        evaluator=EvaluatorReference("praxile-content-review", "1", "rubric.json", "sha256:" + sha(rubric)),
        inputs=(InputArtifact("source.txt", "original-source.txt", "sha256:" + sha(source)),),
        outputs=(OutputContract("output.txt", media_type),))
    report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
        "task_fingerprint": task.fingerprint, "image": task.environment.revision,
        "status": "executor_returned", "cleanup_verified": True, "required_outputs_collected": True,
        "public_inputs": [{"path": "inputs/source.txt", "content_digest": task.inputs[0].content_digest}],
        "source_result_sha256": sha(trajectory),
        "outputs": [{"path": "output.txt", "required": True, "status": "collected", "sha256": sha(output), "bytes": len(output)}]}
    (execution / "execution.json").write_text(json.dumps(report))
    options = dict(execution_dir=execution, evaluator_root=evaluator, source_path="source.txt", output_path="output.txt")
    return task, options


def test_binding_uses_verified_bytes_and_does_not_reveal_locations(tmp_path):
    task, options = setup(tmp_path)
    inputs, binding = prepare_owned_review(task, **options)
    assert inputs["source_text"] == "retain the source"
    assert inputs["output_text"] == "draft output"
    assert inputs["requirements"][0]["requirement"] == "PRIVATE_JUDGE_REQUIREMENT"
    assert len(binding) == 7
    assert all(len(value) == 64 for value in binding.values())
    assert "rubric.json" not in json.dumps(binding)


def selected_fixture(tmp_path):
    from praxile.eval.five_arm_plan import compile_plan, ARMS, IDENTITIES, iter_schedule, plan_digest
    from praxile.eval.five_arm_selection import selection_policy, select_delivered
    from praxile.eval.v2.schema import canonical_json
    task, options = setup(tmp_path)
    root = tmp_path / "cell"
    (root / "attempt-0").mkdir(parents=True)
    execution = root / "attempt-0/lease"
    options["execution_dir"].rename(execution)
    policy = selection_policy("first_delivered")
    identities = {key: "sha256:" + "a" * 64 for key in IDENTITIES}
    identities["terminal_selection"] = "sha256:" + sha(canonical_json(policy).encode())
    plan = compile_plan({"study_id": "bound", "task_ids": [task.task_id], "heldout_task_ids": ["heldout"],
        "repetitions": 1, "repeat_attempts": 2, "identities": identities,
        "budget": {"total_tokens": 300, "model_calls": 3, "wall_seconds": 60}, "arm_order": list(ARMS)})
    row = next(iter_schedule(plan))
    attempts = [{"index": 0, "status": "returned", "report": "attempt-0/lease/execution.json",
                 "sha256": sha((execution / "execution.json").read_bytes())}]
    cell = {"schema_version": "praxile.five_arm_execution.v1", "plan_digest": plan_digest(plan),
        "cell": row, "task_fingerprint": task.fingerprint, "status": "executed_not_scored",
        "terminal_selection_policy": policy, "attempts": attempts, "selected_attempt": 0,
        "selection": select_delivered(attempts, root, policy)}
    (root / "cell.json").write_text(json.dumps(cell))
    return plan, row["cell_id"], task, dict(cell_directory=root,
        cell_sha256=sha((root / "cell.json").read_bytes()), evaluator_root=options["evaluator_root"],
        source_path=options["source_path"], output_path=options["output_path"])


def test_selected_artifact_reaches_broker_judge_with_bound_receipts(tmp_path, monkeypatch):
    from praxile.eval.five_arm_review import review_selected_cell
    from praxile.eval.credential_broker import ChatCredentialBroker
    from praxile.eval.persistent_chat_budget import PersistentChatBudget
    plan, cell_id, task, kwargs = selected_fixture(tmp_path)
    quota = PersistentChatBudget.create(kwargs["cell_directory"] / "quota.sqlite",
        max_calls=2, output_budget=200, ttl_seconds=60)
    quota.reserve("executor", 100)
    key = tmp_path / "private-key"
    key.write_text("fixture-provider-secret")
    key.chmod(0o600)
    calls = []
    def forward(self, raw):
        body = json.loads(raw)
        calls.append(body)
        assert json.loads(body["messages"][1]["content"])["output_units"][0]["text"] == "draft output"
        return {"choices": [{"message": {"tool_calls": [{"type": "function", "function": {
            "name": body["tool_choice"]["function"]["name"], "arguments": json.dumps({"items": [
                {"id": "r", "verdict": "uncertain", "output_refs": [], "reason": "Not enough evidence"}]})}}]}}]}
    monkeypatch.setattr(ChatCredentialBroker, "_forward", forward)
    result = review_selected_cell(plan, cell_id, task, **kwargs, directory=tmp_path / "judge",
        credential_file=key, endpoint="https://provider.invalid/v1/chat/completions", model="fixture",
        total_output_tokens=100, public=True)
    assert len(calls) == 1
    manifest = json.loads((result.parent / "manifest.json").read_text())
    assert manifest["selection_binding"]["cell_sha256"] == kwargs["cell_sha256"]
    assert manifest["selection_binding"]["execution_sha256"] == manifest["execution_binding"]["execution_sha256"]
    assert quota.status()["roles"]["judge"]["reserved_calls"] == 1
    assert manifest["promotion_eligible"] is False


@pytest.mark.parametrize("defect", ["cell", "selected", "output", "trajectory", "requirements", "policy"])
def test_selected_review_rejects_changed_evidence_before_dispatch(tmp_path, monkeypatch, defect):
    from praxile.eval.five_arm_review import review_selected_cell
    plan, cell_id, task, kwargs = selected_fixture(tmp_path)
    root = kwargs["cell_directory"]
    if defect in {"cell", "selected"}:
        path = root / "cell.json"
        cell = json.loads(path.read_text())
        cell["selected_attempt"] = 1
        path.write_text(json.dumps(cell))
        if defect == "selected":
            kwargs["cell_sha256"] = sha(path.read_bytes())
    elif defect == "policy":
        plan["spec"]["identities"]["terminal_selection"] = "sha256:" + "b" * 64
    else:
        path = {"output": root / "attempt-0/lease/outputs/output.txt",
                "trajectory": root / "attempt-0/lease/agent/praxile-result.json",
                "requirements": kwargs["evaluator_root"] / "rubric.json"}[defect]
        path.write_text("changed")
    judge = Mock()
    monkeypatch.setattr("praxile.eval.broker_content_review.run_broker_content_review", judge)
    with pytest.raises(ValueError):
        review_selected_cell(plan, cell_id, task, **kwargs, directory=tmp_path / "judge",
            credential_file=tmp_path / "unused", endpoint="https://provider.invalid/v1/chat/completions",
            model="fixture", total_output_tokens=100, public=True)
    judge.assert_not_called()
    assert not (tmp_path / "judge").exists()


def test_selected_requirements_cannot_be_inside_cell_via_parent_root(tmp_path):
    from praxile.eval.five_arm_review import prepare_selected_cell_review
    plan, cell_id, task, kwargs = selected_fixture(tmp_path)
    task = replace(task, evaluator=replace(task.evaluator, private_ref="cell/requirements.json"))
    kwargs["evaluator_root"] = tmp_path
    with pytest.raises(ValueError, match="outside all cell executions"):
        prepare_selected_cell_review(plan, cell_id, task, **kwargs)


@pytest.mark.parametrize("location", ["inputs/source.txt", "outputs/output.txt", "agent/praxile-result.json", "rubric.json"])
def test_changed_evidence_rejected_before_judge(tmp_path, monkeypatch, location):
    task, options = setup(tmp_path)
    root = options["evaluator_root"] if location == "rubric.json" else options["execution_dir"]
    (root / location).write_text("changed")
    judge = Mock()
    monkeypatch.setattr("praxile.eval.owned_content_review.run_content_review", judge)
    with pytest.raises(ValueError):
        review_owned_output(None, task, **options, run_id="review", total_output_tokens=100)
    judge.assert_not_called()


@pytest.mark.parametrize("field,value", [("cleanup_verified", False), ("status", "failed"),
    ("task_fingerprint", "wrong"), ("required_outputs_collected", False)])
def test_incomplete_execution_cannot_be_reviewed(tmp_path, field, value):
    task, options = setup(tmp_path)
    path = options["execution_dir"] / "execution.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        prepare_owned_review(task, **options)


def test_links_and_unsupported_official_evaluator_rejected(tmp_path):
    task, options = setup(tmp_path)
    with pytest.raises(ValueError, match="explicit development"):
        prepare_owned_review(replace(task, evaluator=replace(task.evaluator, name="official")), **options)
    output = options["execution_dir"] / "outputs/output.txt"
    output.unlink()
    output.symlink_to(options["execution_dir"] / "inputs/source.txt")
    with pytest.raises(ValueError):
        prepare_owned_review(task, **options)


def test_private_evaluator_must_not_be_inside_execution_storage(tmp_path):
    task, options = setup(tmp_path)
    options["evaluator_root"] = options["execution_dir"] / "inputs"
    with pytest.raises(ValueError, match="outside the execution"):
        prepare_owned_review(task, **options)


@pytest.mark.parametrize("output_format", ["text", "docx_body", "pdf_text"])
@pytest.mark.parametrize("source_format", ["text", "docx_body", "pdf_text"])
def test_review_persists_binding_and_keeps_judge_separate(tmp_path, monkeypatch, output_format, source_format):
    media = {"text": "text/plain", "pdf_text": "application/pdf",
             "docx_body": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[output_format]
    task, options = setup(tmp_path, media_type=media)
    options.update(output_format=output_format, source_format=source_format)
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_docx_body", lambda raw:
            (raw.decode(), {"profile": "praxile.docx_body_text.v1", "parser_version": "1.2.0",
                            "worker_sha256": "c" * 64, "text_sha256": sha(raw)}))
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_pdf_text", lambda raw:
            (raw.decode(), {"profile": "praxile.pdf_page_text.v1", "parser_version": "6.0.0",
                            "worker_sha256": "c" * 64, "text_sha256": sha(raw)}))
    judge_root = tmp_path / "judge"
    judge_root.mkdir()
    config = Config.load(judge_root)
    config.data["model_roles"]["content_review_judge"] = {"provider": "fixture", "model": "judge-only"}
    config.data["model_providers"] = {"fixture": {"type": "openai_compatible", "base_url": "http://127.0.0.1:1"}}
    router = Mock()
    router.describe_route.return_value = {"provider": "fixture", "model": "judge-only"}
    close = Mock()
    router.providers = {"fixture": SimpleNamespace(transport=SimpleNamespace(close=close))}
    router.chat.return_value = {"content": json.dumps({"items": [{"id": "r", "verdict": "uncertain",
        "output_refs": [], "reason": "Insufficient evidence"}]}), "usage": {"prompt_tokens": 5, "completion_tokens": 8}}
    monkeypatch.setattr("praxile.eval.content_review_run.ModelRouter", Mock(return_value=router))
    result = review_owned_output(config, task, **options, run_id="bound", total_output_tokens=100)
    assert result["status"] == "reviewed" and result["promotion_eligible"] is False
    manifest = json.loads((config.paths.state / "eval/v2/content_reviews/bound/manifest.json").read_text())
    assert manifest["execution_binding"] == prepare_owned_review(task, **options)[1]
    assert "PRIVATE_JUDGE_REQUIREMENT" in json.dumps(router.chat.call_args.args)
    assert manifest["judge_identity"]["model"] == "judge-only"
    close.assert_called_once()
    with pytest.raises(FileExistsError):
        review_owned_output(config, task, **options, run_id="bound", total_output_tokens=100)
    assert router.chat.call_count == 1
    from praxile.eval.content_review_files import load_review_run
    path = config.paths.state / "eval/v2/content_reviews/bound"
    load_review_run(path)
    if source_format != "text":
        extraction = manifest["execution_binding"]["source_extraction"]
        original = extraction["text_sha256"]
        extraction["text_sha256"] = "e" * 64
        (path / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="source extraction binding"):
            load_review_run(path)
        extraction["text_sha256"] = original
    if output_format != "text":
        extraction = manifest["execution_binding"]["output_extraction"]
        original = extraction["text_sha256"]
        extraction["text_sha256"] = "d" * 64
        (path / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="output extraction binding"):
            load_review_run(path)
        extraction["text_sha256"] = original
    manifest["execution_binding"]["output_sha256"] = "invalid"
    (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="execution evidence binding"):
        load_review_run(path)


@pytest.mark.parametrize("profile,media", [("pdf_text", "application/pdf"),
    ("docx_body", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
def test_binary_profile_requires_opt_in_and_verified_bytes(tmp_path, monkeypatch, profile, media):
    task, options = setup(tmp_path, media_type=media)
    extract = Mock()
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_" + profile, extract)
    with pytest.raises(ValueError, match="binary output"):
        prepare_owned_review(task, **options)
    (options["execution_dir"] / "outputs/output.txt").write_text("tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_owned_review(task, **options, output_format=profile)
    extract.assert_not_called()


@pytest.mark.parametrize("profile", ["docx_body", "pdf_text"])
def test_source_bytes_verified_before_extraction(tmp_path, monkeypatch, profile):
    task, options = setup(tmp_path)
    extract = Mock()
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_" + profile, extract)
    with pytest.raises(ValueError, match="source extraction profile"):
        prepare_owned_review(task, **options, source_format="auto")
    (options["execution_dir"] / "inputs/source.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_owned_review(task, **options, source_format=profile)
    extract.assert_not_called()


@pytest.mark.parametrize("damage", ["none", "input", "output", "optional", "size", "missing", "unknown_status"])
def test_all_declared_artifacts_verified_before_judge(tmp_path, monkeypatch, damage):
    task, options = setup(tmp_path)
    root = options["execution_dir"]
    task = replace(task, inputs=task.inputs + (InputArtifact("extra-input.txt", "extra-source", "sha256:" + sha(b"input")),),
        outputs=task.outputs + (OutputContract("extra.txt", "text/plain"), OutputContract("optional.txt", "text/plain", required=False)))
    (root / "inputs/extra-input.txt").write_bytes(b"input")
    (root / "outputs/extra.txt").write_bytes(b"output")
    (root / "outputs/optional.txt").write_bytes(b"optional")
    path = root / "execution.json"
    report = json.loads(path.read_text())
    report["task_fingerprint"] = task.fingerprint
    report["public_inputs"] = [{"path": "inputs/" + item.path, "content_digest": item.content_digest} for item in task.inputs]
    report["outputs"] += [
        {"path": "extra.txt", "required": True, "status": "collected", "bytes": 6, "sha256": sha(b"output")},
        {"path": "optional.txt", "required": False, "status": "collected", "bytes": 8, "sha256": sha(b"optional")}]
    if damage in {"input", "output", "optional"}:
        filename = {"input": "inputs/extra-input.txt", "output": "outputs/extra.txt", "optional": "outputs/optional.txt"}[damage]
        (root / filename).write_bytes(b"tampered")
    elif damage == "size":
        report["outputs"][1]["bytes"] = 5
    elif damage == "missing":
        (root / "outputs/extra.txt").unlink()
    elif damage == "unknown_status":
        report["outputs"][2]["status"] = "ignored"
    path.write_text(json.dumps(report))
    judge = Mock()
    monkeypatch.setattr("praxile.eval.owned_content_review.run_content_review", judge)
    if damage != "none":
        with pytest.raises((OSError, ValueError)):
            review_owned_output(None, task, **options, run_id="multi", total_output_tokens=100)
        judge.assert_not_called()
    else:
        inputs, first = prepare_owned_review(task, **options)
        assert inputs["source_text"] == "retain the source" and inputs["output_text"] == "draft output"
        assert set(inputs) == {"source_text", "output_text", "requirements"}
        (root / "outputs/optional.txt").unlink()
        report["outputs"][2] = {"path": "optional.txt", "required": False, "status": "unavailable"}
        path.write_text(json.dumps(report))
        _, second = prepare_owned_review(task, **options)
        assert first["artifact_set_sha256"] != second["artifact_set_sha256"]


def test_artifact_collection_change_affects_same_output_comparison_only():
    from praxile.eval.content_review_files import check_execution_conditions
    fields = {key: "a" * 64 for key in ("task_fingerprint", "source_sha256", "evaluator_sha256", "output_sha256")}
    left = {"execution_binding": dict(fields, artifact_set_sha256="b" * 64)}
    right = {"execution_binding": dict(fields, artifact_set_sha256="c" * 64)}
    assert check_execution_conditions({"status": "compared"}, left, right)["status"] == "compared"
    result = check_execution_conditions({"status": "compared", "rows": ["invalid"]}, left, right, same_output=True)
    assert result["status"] == "inconclusive" and "rows" not in result
    assert result["execution_conditions"]["different_fields"] == ["artifact_set_sha256"]
    del right["execution_binding"]["artifact_set_sha256"]
    assert check_execution_conditions({"status": "compared"}, left, right)["status"] == "inconclusive"
