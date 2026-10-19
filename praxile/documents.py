"""Bounded document reading, separate from text-file and shell tools."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .environment import Observation
from .document_spec import validate_docx_spec


def create_docx(config, safety, path, spec):
    decision = safety.check_path(path, write=True)
    if not decision.allowed:
        return Observation("blocked", decision.reason, risk_level=decision.risk_level)
    try:
        validate_docx_spec(spec)
        root = config.paths.root.resolve()
        target = root / path
        if (target.suffix.lower() != ".docx" or not target.resolve().is_relative_to(root)
                or target.is_symlink() or not target.parent.is_dir()):
            return Observation("blocked", "DOCX requires an existing project directory and .docx target")
        if target.exists():
            return Observation("blocked", "create_docx never overwrites an existing file; use a new output path")
        with tempfile.TemporaryDirectory(prefix="praxile-docx-") as directory:
            source = Path(directory) / "spec.json"
            source.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            with tempfile.TemporaryFile() as output:
                process = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("document_worker.py")),
                    str(source), '{"operation":"create_docx"}'], cwd=directory,
                    stdout=output, stderr=subprocess.DEVNULL, timeout=25)
                if process.returncode:
                    return Observation("failure", "DOCX writer failed or exceeded resource limits")
                output.seek(0)
                report = json.loads(output.read(1024 * 1024))
            if report["status"] != "success":
                return Observation("failure", report["error"])
            content = (Path(directory) / "result.docx").read_bytes()
        # Publish a complete file atomically, without an overwrite race.
        staging = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".praxile-docx-", delete=False) as handle:
                staging = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(staging, target)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
        return Observation("success", "Created DOCX; visual layout and factual quality remain unverified.",
            {"path": path, "changed": True, "before_exists": False, "backup_path": None,
             "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
             "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    except subprocess.TimeoutExpired:
        return Observation("failure", "DOCX writer timed out after 25 seconds")
    except (OSError, ValueError, KeyError, TypeError):
        return Observation("failure", "Unable to create DOCX; validate spec, destination and dependencies")


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
