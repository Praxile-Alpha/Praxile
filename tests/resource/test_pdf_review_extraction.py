import hashlib
import io
import json

import pytest

from praxile.eval.document_review_extraction import extract_pdf_text
from praxile.eval.content_review_run import validate_execution_binding

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def pdf_bytes(texts, *, password=None):
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = pypdf.PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 72 720 Td (" + text.encode("ascii") + b") Tj ET")
        page[NameObject("/Contents")] = content
    if password:
        writer.encrypt(password)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_real_pdf_parser_preserves_page_order_and_binds_text():
    raw = pdf_bytes(["First page deadline Friday", "Second page budget 100"])
    text, extraction = extract_pdf_text(raw)
    assert text == "First page deadline Friday\nSecond page budget 100"
    assert extraction["profile"] == "praxile.pdf_page_text.v1"
    assert extraction["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    binding = {key: "a" * 64 for key in ("task_fingerprint", "execution_sha256", "trajectory_sha256",
        "source_sha256", "output_sha256", "evaluator_sha256")}
    binding["source_extraction"] = extraction
    validate_execution_binding(binding, source_text=text)
    with pytest.raises(ValueError, match="extraction binding"):
        validate_execution_binding(binding, source_text="changed")


@pytest.mark.parametrize("kind", ["encrypted", "blank", "mixed_blank", "pages", "text", "invalid", "bytes"])
def test_pdf_rejects_unreviewable_inputs_without_truncation(kind):
    if kind == "encrypted":
        raw = pdf_bytes(["secret"], password="password")
    elif kind == "blank":
        raw = pdf_bytes([""])
    elif kind == "mixed_blank":
        raw = pdf_bytes(["readable", ""])
    elif kind == "pages":
        raw = pdf_bytes(["page"] * 101)
    elif kind == "text":
        raw = pdf_bytes(["x" * 200001])
    else:
        raw = b"x" * (1024**2 + 1) if kind == "bytes" else b"not pdf"
    with pytest.raises(ValueError):
        extract_pdf_text(raw)


def test_real_pdf_bridge_verifies_raw_artifacts_before_text_review(tmp_path):
    from praxile.eval.contracts import BenchmarkTask, EnvironmentSpec, EvaluatorReference, InputArtifact, OutputContract
    from praxile.eval.owned_content_review import prepare_owned_review
    execution, private = tmp_path / "execution", tmp_path / "private"
    private.mkdir()
    for name in ("inputs", "outputs", "agent"):
        (execution / name).mkdir(parents=True)
    source, output = pdf_bytes(["Deadline Friday"]), pdf_bytes(["Deliver Friday"])
    trajectory = b'{"schema_version":"praxile.native_benchmark_run.v1"}'
    evaluator = b'{"requirements":[{"id":"r","requirement":"Keep deadline","source_quote":"Deadline Friday"}]}'
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    (execution / "inputs/source.pdf").write_bytes(source)
    (execution / "outputs/answer.pdf").write_bytes(output)
    (execution / "agent/praxile-result.json").write_bytes(trajectory)
    (private / "rubric.json").write_bytes(evaluator)
    task = BenchmarkTask(task_id="pdf-fixture", benchmark="fixture", dataset_revision="1", split="development",
        instruction="Retain deadline", environment=EnvironmentSpec("harbor", "fixture", "image"),
        evaluator=EvaluatorReference("praxile-content-review", "1", "rubric.json", "sha256:" + sha(evaluator)),
        inputs=(InputArtifact("source.pdf", "original.pdf", "sha256:" + sha(source)),),
        outputs=(OutputContract("answer.pdf", "application/pdf"),))
    report = {"schema_version": "praxile.owned_native_execution.v1", "task_id": task.task_id,
        "task_fingerprint": task.fingerprint, "image": "image", "status": "executor_returned",
        "cleanup_verified": True, "required_outputs_collected": True, "source_result_sha256": sha(trajectory),
        "public_inputs": [{"path": "inputs/source.pdf", "content_digest": "sha256:" + sha(source)}],
        "outputs": [{"path": "answer.pdf", "required": True, "status": "collected", "sha256": sha(output), "bytes": len(output)}]}
    (execution / "execution.json").write_text(json.dumps(report))
    inputs, binding = prepare_owned_review(task, execution_dir=execution, evaluator_root=private,
        source_path="source.pdf", output_path="answer.pdf", source_format="pdf_text", output_format="pdf_text")
    assert inputs["source_text"] == "Deadline Friday"
    assert inputs["output_text"] == "Deliver Friday"
    assert binding["source_sha256"] == sha(source)
    assert binding["output_sha256"] == sha(output)
    validate_execution_binding(binding, source_text=inputs["source_text"], output_text=inputs["output_text"])
