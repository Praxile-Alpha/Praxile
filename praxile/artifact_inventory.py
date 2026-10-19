"""Observe submitted artifacts without inventing a user acceptance contract."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def inspect_outputs(root: Path, directory: str) -> dict:
    report = {"schema_version": "praxile.artifact_inventory.v1", "source": "observed_outputs",
              "directory": directory, "status": "blocked", "files": [], "complete": False,
              "quality_verified": False, "required_outputs_verified": False,
              "official_evaluation": "not_run"}
    root = root.resolve(strict=True)
    relative = Path(directory)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return report
    selected = root / relative
    if selected.resolve() != selected:
        return report
    if not selected.is_dir():
        report["status"] = "missing"
        return report
    deadline = time.monotonic() + 30
    pending, count = [selected], 0
    try:
        while pending:
            parent = pending.pop()
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > 200 or time.monotonic() >= deadline:
                        report["status"] = "incomplete"
                        return report
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                        continue
                    item = {"path": path.relative_to(root).as_posix(), "status": "failed"}
                    report["files"].append(item)
                    if entry.is_symlink() or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                        item["reason"] = "symlink_or_special_file"
                        continue
                    if path.resolve() != path:
                        item["reason"] = "path_changed"
                        continue
                    with path.open("rb") as source:
                        raw = source.read(10_000_001)
                    if len(raw) > 10_000_000:
                        item["reason"] = "size_limit"
                        continue
                    item.update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                    if not raw:
                        item["reason"] = "empty_file"
                        continue
                    suffix = path.suffix.lower().lstrip(".")
                    if suffix in {"docx", "xlsx", "pptx", "pdf"}:
                        with tempfile.TemporaryDirectory(prefix="praxile-output-inspection-") as tmp:
                            snapshot = Path(tmp) / ("artifact." + suffix)
                            snapshot.write_bytes(raw)
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                report["status"] = "incomplete"
                                return report
                            with tempfile.TemporaryFile() as output:
                                try:
                                    child = subprocess.run([sys.executable, "-I",
                                        str(Path(__file__).with_name("artifact_openers.py")), str(snapshot), suffix],
                                        stdout=output, stderr=subprocess.DEVNULL, timeout=min(15, remaining))
                                except subprocess.TimeoutExpired:
                                    item["reason"] = "parser_timeout"
                                    continue
                                output.seek(0)
                                payload = output.read(1_000_001)
                            if child.returncode or len(payload) > 1_000_000:
                                item["reason"] = "parser_process_failed"
                                continue
                            parsed = json.loads(payload)
                            if not isinstance(parsed, dict) or parsed.get("status") not in {"passed", "failed", "unsupported"}:
                                item["reason"] = "invalid_parser_response"
                                continue
                            item.update(parsed)
                    elif suffix in {"txt", "md", "json"}:
                        try:
                            text = raw.decode("utf-8")
                            if not text.strip():
                                raise ValueError("empty")
                            if suffix == "json":
                                json.loads(text, parse_constant=_reject_constant)
                            item["status"] = "passed"
                        except (ValueError, RecursionError):
                            item["reason"] = "invalid_text_or_json"
                    else:
                        item.update(status="unsupported", reason="format_not_inspected")
        report["complete"] = True
        report["status"] = ("empty" if not report["files"] else "clean_structure"
                            if all(item["status"] == "passed" for item in report["files"]) else "issues")
    except (OSError, ValueError, TypeError):
        report["status"] = "inspection_error"
    return report
