"""Bounded opt-in DOCX body extraction from already verified artifact bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def extract_docx_body(raw):
    if not isinstance(raw, bytes) or len(raw) > 1024**2:
        raise ValueError("DOCX review input exceeds 1 MiB")
    worker = Path(__file__).parents[1] / "document_worker.py"
    with tempfile.TemporaryDirectory(prefix="praxile-review-docx-") as directory:
        source = Path(directory) / "output.docx"
        source.write_bytes(raw)
        try:
            process = subprocess.run([sys.executable, "-I", str(worker), str(source),
                json.dumps({"operation": "review_docx_body"})], capture_output=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("DOCX review extraction failed or timed out") from error
    if process.returncode or len(process.stdout) > 2 * 1024**2:
        raise ValueError("DOCX review extraction failed or exceeded output limit")
    result = json.loads(process.stdout)
    if (not isinstance(result, dict) or result.get("status") != "success"
            or not isinstance(result.get("text"), str) or len(result["text"]) > 200000
            or not isinstance(result.get("parser_version"), str)):
        raise ValueError("DOCX extraction unavailable; install praxile[documents] and check document bounds")
    text = result["text"]
    return text, {"profile": "praxile.docx_body_text.v1", "parser_version": result["parser_version"],
        "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
