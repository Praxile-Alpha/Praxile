"""Exclusive run IDs and verified terminal reuse, never automatic crash replay."""
import hashlib
import json
import os
from pathlib import Path
import re

from .artifact_read import read_artifact
from ..utils import write_json


def validate_run_id(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", run_id):
        raise ValueError("unsafe native run ID")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class NativeRunClaim:
    def __init__(self, state_root, run_id, plan):
        validate_run_id(run_id)
        self.root = Path(state_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.root / "run-claims"
        if directory.is_symlink():
            raise ValueError("run claim directory must not be a link")
        directory.mkdir(mode=0o700, exist_ok=True)
        self.path = directory / (run_id + ".json")
        self.identity = _sha(json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        self.record = {"schema_version": "praxile.native_run_claim.v1", "run_id": run_id,
                       "plan_sha256": self.identity, "status": "claimed"}
        info = self.root.stat()
        self.root_identity = info.st_dev, info.st_ino

    def _read(self, relative):
        return read_artifact(self.root, relative, root_identity=self.root_identity)

    def acquire(self):
        with os.fdopen(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as handle:
            handle.write(json.dumps(self.record, sort_keys=True).encode())
            handle.flush()
            os.fsync(handle.fileno())

    def bind_report(self, path):
        relative = Path(path).relative_to(self.root).as_posix()
        if not re.fullmatch(r"harbor-lease-[a-f0-9]{32}/execution.json", relative):
            raise ValueError("report must belong to an owned native lease")
        self.record["report"] = relative
        write_json(self.path, self.record)

    def complete(self, expected_files):
        prefix = self.record["report"].split("/")[0] + "/"
        files = {}
        for relative, expected in expected_files.items():
            if not relative.startswith(prefix):
                raise ValueError("completion evidence must belong to the same lease")
            actual = _sha(self._read(relative))
            if actual != expected:
                raise ValueError("completion evidence changed before sealing")
            files[relative] = actual
        if self.record["report"] not in files or prefix + "agent/praxile-result.json" not in files:
            raise ValueError("completion requires report and trajectory")
        self.record.update(status="completed", files=files)
        write_json(self.path, self.record)

    def resume(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate claim field")
                result[key] = value
            return result
        record = json.loads(self._read(self.path.relative_to(self.root).as_posix()), object_pairs_hook=unique)
        if (not isinstance(record, dict) or record.get("schema_version") != self.record["schema_version"]
                or record.get("run_id") != self.record["run_id"] or record.get("plan_sha256") != self.identity):
            raise ValueError("run claim does not match frozen execution plan")
        if record.get("status") != "completed":
            raise ValueError("run has no verified completion; automatic replay forbidden")
        report = record.get("report")
        if not isinstance(report, str) or not re.fullmatch(r"harbor-lease-[a-f0-9]{32}/execution.json", report):
            raise ValueError("invalid completion report path")
        files = record.get("files")
        prefix = report.split("/")[0] + "/"
        if not isinstance(files, dict) or report not in files or prefix + "agent/praxile-result.json" not in files:
            raise ValueError("incomplete completion evidence")
        for relative, checksum in files.items():
            if (not relative.startswith(prefix) or not isinstance(checksum, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", checksum) or _sha(self._read(relative)) != checksum):
                raise ValueError("completion evidence changed")
        result = json.loads(self._read(report))
        if (not isinstance(result, dict) or result.get("status") != "executor_returned" or result.get("cleanup_verified") is not True
                or result.get("required_outputs_collected") is not True):
            raise ValueError("run completion is not verified")
        return self.root / report
