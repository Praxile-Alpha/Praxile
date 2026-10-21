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
    assert len(binding) == 6
    assert all(len(value) == 64 for value in binding.values())
    assert "rubric.json" not in json.dumps(binding)


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


@pytest.mark.parametrize("docx_body", [False, True])
@pytest.mark.parametrize("source_docx", [False, True])
def test_review_persists_binding_and_keeps_judge_separate(tmp_path, monkeypatch, docx_body, source_docx):
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if docx_body else "text/plain"
    task, options = setup(tmp_path, media_type=media)
    if docx_body:
        options["output_format"] = "docx_body"
    if source_docx:
        options["source_format"] = "docx_body"
    if docx_body or source_docx:
        monkeypatch.setattr("praxile.eval.document_review_extraction.extract_docx_body", lambda raw:
            (raw.decode(), {"profile": "praxile.docx_body_text.v1", "parser_version": "1.2.0",
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
    if source_docx:
        extraction = manifest["execution_binding"]["source_extraction"]
        original = extraction["text_sha256"]
        extraction["text_sha256"] = "e" * 64
        (path / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="source extraction binding"):
            load_review_run(path)
        extraction["text_sha256"] = original
    if docx_body:
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


def test_docx_profile_requires_opt_in_and_verified_bytes(tmp_path, monkeypatch):
    task, options = setup(tmp_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    extract = Mock()
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_docx_body", extract)
    with pytest.raises(ValueError, match="binary output"):
        prepare_owned_review(task, **options)
    (options["execution_dir"] / "outputs/output.txt").write_text("tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_owned_review(task, **options, output_format="docx_body")
    extract.assert_not_called()


def test_source_bytes_verified_before_extraction(tmp_path, monkeypatch):
    task, options = setup(tmp_path)
    extract = Mock()
    monkeypatch.setattr("praxile.eval.document_review_extraction.extract_docx_body", extract)
    with pytest.raises(ValueError, match="source extraction profile"):
        prepare_owned_review(task, **options, source_format="auto")
    (options["execution_dir"] / "inputs/source.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        prepare_owned_review(task, **options, source_format="docx_body")
    extract.assert_not_called()
