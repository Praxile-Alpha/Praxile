"""Bounded document reading, separate from text-file and shell tools."""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .environment import Observation


def read_document(config, safety, path, *, start=1, limit=5, sheet=None, char_offset=0):
    if (type(start) is not int or not 1 <= start <= 100000
            or type(limit) is not int or not 1 <= limit <= 20
            or type(char_offset) is not int or not 0 <= char_offset <= 10000000
            or (sheet is not None and (not isinstance(sheet, str) or len(sheet) > 128))):
        return Observation("failure", "Invalid document pagination options")
    decision = safety.check_path(path, write=False)
    if not decision.allowed:
        return Observation("blocked", decision.reason, risk_level=decision.risk_level)
    target = (config.paths.root / path).resolve()
    if target.suffix.lower() not in {".pdf", ".xlsx", ".docx"}:
        return Observation("failure", "read_document supports PDF, XLSX and DOCX only")
    try:
        if not target.is_relative_to(config.paths.root.resolve()) or not target.is_file():
            return Observation("blocked", "Document must be a project file")
        with target.open("rb") as source:
            raw = source.read(20 * 1024 * 1024 + 1)
        if len(raw) > 20 * 1024 * 1024:
            return Observation("failure", "Document exceeds 20 MiB input limit")
        digest = hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory(prefix="praxile-document-") as directory:
            snapshot = Path(directory) / ("input" + target.suffix.lower())
            snapshot.write_bytes(raw)
            with tempfile.TemporaryFile() as output:
                process = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("document_worker.py")),
                    str(snapshot), json.dumps({"start": start, "limit": limit, "sheet": sheet, "offset": char_offset})],
                    stdout=output, stderr=subprocess.DEVNULL, timeout=25, cwd=directory)
                if process.returncode:
                    return Observation("failure", "Document parser exited unsuccessfully or exceeded resource limits")
                output.seek(0)
                payload = output.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                return Observation("failure", "Document parser output exceeded limit")
            report = json.loads(payload)
        if report["status"] != "success":
            return Observation("failure", report["error"])
        data = {**report["data"], "path": path, "source_sha256": digest}
        return Observation("success", json.dumps(data, ensure_ascii=False), data)
    except subprocess.TimeoutExpired:
        return Observation("failure", "Document parser timed out after 25 seconds")
    except (OSError, ValueError, KeyError):
        return Observation("failure", "Unable to read document")
