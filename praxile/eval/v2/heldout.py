from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...utils import file_lock, utc_now
from .capability import CapabilityProtocol
from .schema import EvalSchemaError, canonical_json


class HeldoutUseLedger:
    """Reserve one local held-out set for one frozen experiment."""

    def __init__(self, state_root: Path):
        self.root = state_root.resolve() / "eval" / "v2" / "heldout-claims"

    def _path(self, protocol: CapabilityProtocol) -> Path:
        evaluation = protocol.evaluation
        identity = {
            "dataset_name": evaluation.dataset_name,
            "split": evaluation.split,
            "task_ids": sorted(evaluation.heldout_task_ids),
        }
        digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def reserve(
        self, protocol: CapabilityProtocol, *, experiment_id: str,
        candidate_digest: str, task_set_digest: str,
    ) -> dict[str, Any]:
        path = self._path(protocol)
        task_ids = set(protocol.evaluation.heldout_task_ids)
        with file_lock(self.root / "claims"):
            if path.exists():
                claim = json.loads(path.read_text(encoding="utf-8"))
                expected = (experiment_id, protocol.digest, candidate_digest, task_set_digest)
                actual = tuple(claim.get(key) for key in (
                    "experiment_id", "protocol_digest", "candidate_digest", "task_set_digest"
                ))
                if actual != expected:
                    raise EvalSchemaError(
                        "held-out task set was already reserved for another experiment or candidate; "
                        "use genuinely unseen tasks"
                    )
                return claim
            for other_path in self.root.glob("*.json"):
                other = json.loads(other_path.read_text(encoding="utf-8"))
                if "heldout_task_ids" not in other:
                    raise EvalSchemaError("existing held-out claim lacks task IDs; cannot prove non-overlap")
                if task_ids & set(other["heldout_task_ids"]):
                    raise EvalSchemaError("held-out task ID was already reserved by another experiment")
            claim = {
                "schema_version": "praxile.heldout_claim.v1",
                "experiment_id": experiment_id,
                "protocol_digest": protocol.digest,
                "candidate_digest": candidate_digest,
                "task_set_digest": task_set_digest,
                "heldout_task_ids": sorted(task_ids),
                "status": "reserved",
                "created_at": utc_now(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(path, claim)
            return claim

    def mark_evaluated(self, protocol: CapabilityProtocol, *, experiment_id: str, report_digest: str) -> None:
        path = self._path(protocol)
        with file_lock(self.root / "claims"):
            if not path.is_file():
                raise EvalSchemaError("held-out claim is missing")
            claim = json.loads(path.read_text(encoding="utf-8"))
            if claim.get("experiment_id") != experiment_id or claim.get("protocol_digest") != protocol.digest:
                raise EvalSchemaError("held-out claim belongs to another experiment")
            if claim.get("status") == "evaluated":
                return
            claim["status"] = "evaluated"
            claim["evaluated_at"] = utc_now()
            claim["report_digest"] = report_digest
            _write_atomic(path, claim)

    def status(self, protocol: CapabilityProtocol) -> str | None:
        path = self._path(protocol)
        if not path.is_file():
            return None
        return str(json.loads(path.read_text(encoding="utf-8")).get("status"))

    def experiment_status(self, experiment_id: str) -> str | None:
        if not self.root.is_dir():
            return None
        for path in self.root.glob("*.json"):
            claim = json.loads(path.read_text(encoding="utf-8"))
            if claim.get("experiment_id") == experiment_id:
                return str(claim.get("status"))
        return None

    def run_is_sealed(self, run_id: str) -> bool:
        for suffix in (".baseline", ".candidate"):
            if run_id.endswith(suffix):
                return self.experiment_status(run_id[:-len(suffix)]) == "reserved"
        return False


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
