"""Opt-in DOCX layout inspection; never rewrites the submitted file."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from .environment import Observation


def check_layout(config, safety, path, *, max_pages=None):
    if max_pages is not None and (type(max_pages) is not int or not 1 <= max_pages <= 2000):
        return Observation("failure", "max_pages must be an integer in [1, 2000]")
    decision = safety.check_path(path, write=False)
    if not decision.allowed:
        return Observation("blocked", decision.reason, risk_level=decision.risk_level)
    executable = config.get("documents", "renderer_executable", default="")
    if not isinstance(executable, str) or not executable or not Path(executable).is_absolute():
        return Observation("blocked", "Configure documents.renderer_executable with a trusted absolute LibreOffice path")
    if os.name != "posix":
        return Observation("failure", "Document layout process-group isolation currently requires POSIX")
    try:
        root = config.paths.root.resolve(strict=True)
        target = root / path
        if (target.resolve(strict=True) != target or not target.is_relative_to(root)
                or target.suffix.lower() != ".docx" or not target.is_file()):
            return Observation("blocked", "Layout inspection requires a project DOCX without symlink components")
        with target.open("rb") as source:
            raw = source.read(10_000_001)
        if len(raw) > 10_000_000:
            return Observation("failure", "Layout input exceeds 10 MB")
        with tempfile.TemporaryDirectory(prefix="praxile-layout-") as directory:
            (Path(directory) / "input.docx").write_bytes(raw)
            with tempfile.TemporaryFile() as output:
                child = subprocess.Popen([sys.executable, "-I",
                    str(Path(__file__).with_name("document_layout_worker.py")), executable],
                    cwd=directory, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": directory,
                         "TMPDIR": directory, "LANG": "C.UTF-8"})
                try:
                    child.wait(timeout=45)
                finally:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.wait()
                if child.returncode:
                    return Observation("failure", "Layout worker failed or exceeded resource limits")
                output.seek(0)
                payload = output.read(1_000_001)
            if len(payload) > 1_000_000:
                return Observation("failure", "Layout response exceeded limit")
            report = json.loads(payload)
        if report.get("status") != "success":
            return Observation("failure", report.get("reason", "Layout inspection failed"))
        pages = report.get("pages")
        if type(pages) is not int or not 1 <= pages <= 2000:
            return Observation("failure", "Invalid rendered page count")
        data = {"schema_version": "praxile.document_layout.v1", "path": path,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "rendered_pdf_sha256": report["pdf_sha256"],
            "pages": pages, "max_pages": max_pages,
            "page_limit_met": None if max_pages is None else pages <= max_pages,
            "constraint_source": "action_argument", "renderer_executable": executable,
            "rendered": True, "visual_quality_verified": False, "semantic_quality_verified": False,
            "official_evaluation": False}
        return Observation("failure" if data["page_limit_met"] is False else "success", json.dumps(data), data)
    except subprocess.TimeoutExpired:
        return Observation("failure", "Layout rendering timed out after 45 seconds; process group terminated")
    except (OSError, ValueError, KeyError, TypeError):
        return Observation("failure", "Unable to inspect document layout; check input and renderer dependencies")
