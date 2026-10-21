import io
import hashlib
import json
import zipfile

import pytest

from praxile.eval.document_review_extraction import extract_docx_body
from praxile.eval.content_review_run import validate_execution_binding

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_real_parser_preserves_long_blocks_and_table_order():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    long_text = "x" * 5000
    document.add_paragraph(long_text)
    document.add_table(rows=1, cols=1).cell(0, 0).text = "middle table"
    document.add_paragraph("last paragraph")
    raw = io.BytesIO()
    document.save(raw)
    text, evidence = extract_docx_body(raw.getvalue())
    assert text == long_text + "\nmiddle table\nlast paragraph"
    binding = {key: "a" * 64 for key in ("task_fingerprint", "execution_sha256", "trajectory_sha256",
        "source_sha256", "output_sha256", "evaluator_sha256")}
    binding["output_extraction"] = evidence
    validate_execution_binding(binding, output_text=text)
    with pytest.raises(ValueError, match="extraction binding"):
        validate_execution_binding(binding, output_text="changed")


def test_real_parser_rejects_document_over_text_budget():
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("x" * 200001)
    raw = io.BytesIO()
    document.save(raw)
    with pytest.raises(ValueError, match="extraction unavailable"):
        extract_docx_body(raw.getvalue())


def test_invalid_package_is_not_treated_as_empty_document():
    with pytest.raises(ValueError):
        extract_docx_body(b"not a docx")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("large", b"x" * (64 * 1024**2 + 1))
    with pytest.raises(ValueError):
        extract_docx_body(raw.getvalue())


def test_real_docx_source_and_output_bind_to_frozen_execution(tmp_path):
    from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
    from praxile.eval.owned_content_review import prepare_owned_review
    docx = pytest.importorskip("docx")
    def package(text):
        document = docx.Document()
        document.add_paragraph(text)
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()
    def sha(raw):
        return hashlib.sha256(raw).hexdigest()
    execution, private = tmp_path / "execution", tmp_path / "private"
    private.mkdir()
    for name in ("inputs", "outputs", "agent"):
        (execution / name).mkdir(parents=True)
    source, output = package("Deadline: Friday"), package("Deliver by Friday")
    (execution / "inputs/source.docx").write_bytes(source)
    (execution / "outputs/output.docx").write_bytes(output)
    trajectory = json.dumps({"schema_version": "praxile.native_benchmark_run.v1"}).encode()
    (execution / "agent/praxile-result.json").write_bytes(trajectory)
    rubric = json.dumps({"requirements": [{"id": "r1", "requirement": "Keep deadline",
        "source_quote": "Deadline: Friday"}]}).encode()
    (private / "rubric.json").write_bytes(rubric)
    task = BenchmarkTask(task_id="docx-fixture", benchmark="fixture", dataset_revision="1",
        split="development", instruction="Retain deadline", environment=EnvironmentSpec("harbor", "fixture", "image"),
        evaluator=EvaluatorReference("praxile-content-review", "1", "rubric.json", "sha256:" + sha(rubric)),
        inputs=(InputArtifact("source.docx", "original.docx", "sha256:" + sha(source)),),
        outputs=(OutputContract("output.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),))
    report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
        "task_fingerprint": task.fingerprint, "image": "image", "status": "executor_returned",
        "cleanup_verified": True, "required_outputs_collected": True,
        "source_result_sha256": sha(trajectory),
        "public_inputs": [{"path": "inputs/source.docx", "content_digest": "sha256:" + sha(source)}],
        "outputs": [{"path": "output.docx", "required": True, "status": "collected", "sha256": sha(output), "bytes": len(output)}]}
    (execution / "execution.json").write_text(json.dumps(report))
    args = dict(execution_dir=execution, evaluator_root=private, source_path="source.docx", output_path="output.docx")
    with pytest.raises(UnicodeDecodeError):
        prepare_owned_review(task, **args, output_format="docx_body")
    inputs, binding = prepare_owned_review(task, **args, source_format="docx_body", output_format="docx_body")
    assert inputs["source_text"] == "Deadline: Friday"
    assert inputs["output_text"] == "Deliver by Friday"
    assert binding["source_sha256"] == sha(source)
    assert binding["output_sha256"] == sha(output)
    validate_execution_binding(binding, source_text=inputs["source_text"], output_text=inputs["output_text"])
    assert binding["source_extraction"]["text_sha256"] != binding["output_extraction"]["text_sha256"]
