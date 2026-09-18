from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ...utils import file_lock, utc_now
from .schema import EvalSchemaError, canonical_json


PROXY_EVAL_SCHEMA_VERSION = "praxile.proxy_eval.v1"
_METRICS = {"token_delta", "tool_call_delta", "latency_ms_delta", "cost_delta"}
_OPERATORS = {"<=" , ">="}
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True)
class ProxyCheck:
    metric: str
    operator: str
    threshold: float

    @classmethod
    def from_dict(cls, value: Any) -> "ProxyCheck":
        if not isinstance(value, Mapping) or set(value) != {"metric", "operator", "threshold"}:
            raise EvalSchemaError("proxy check requires metric, operator, and threshold")
        metric, operator, threshold = value["metric"], value["operator"], value["threshold"]
        if metric not in _METRICS or operator not in _OPERATORS:
            raise EvalSchemaError("proxy check uses an unsupported metric or operator")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold):
            raise EvalSchemaError("proxy check threshold must be a finite number")
        return cls(str(metric), str(operator), float(threshold))

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "operator": self.operator, "threshold": self.threshold}


@dataclass(frozen=True)
class ProxyEvalProposal:
    proxy_id: str
    version: str
    hypothesis_id: str
    task_ids: tuple[str, ...]
    rationale: str
    checks: tuple[ProxyCheck, ...]

    @classmethod
    def load(cls, path: Path) -> "ProxyEvalProposal":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, value: Any) -> "ProxyEvalProposal":
        required = {"schema_version", "proxy_id", "version", "hypothesis_id", "task_ids", "rationale", "checks"}
        if not isinstance(value, Mapping) or set(value) != required or value.get("schema_version") != PROXY_EVAL_SCHEMA_VERSION:
            raise EvalSchemaError("invalid proxy eval proposal schema")
        for field in ("proxy_id", "version", "hypothesis_id", "rationale"):
            if not isinstance(value[field], str) or not value[field].strip():
                raise EvalSchemaError(f"proxy eval {field} must be non-empty")
        if not _SAFE_ID.fullmatch(value["proxy_id"]) or not _SAFE_ID.fullmatch(value["version"]):
            raise EvalSchemaError("proxy eval ID/version is unsafe")
        task_ids = value["task_ids"]
        if (not isinstance(task_ids, list) or not task_ids
                or any(not isinstance(item, str) or not _SAFE_ID.fullmatch(item) for item in task_ids)
                or len(task_ids) != len(set(task_ids))):
            raise EvalSchemaError("proxy eval task_ids must be unique, non-empty, safe strings")
        raw_checks = value["checks"]
        if not isinstance(raw_checks, list) or not raw_checks:
            raise EvalSchemaError("proxy eval checks must be a non-empty array")
        checks = tuple(ProxyCheck.from_dict(item) for item in raw_checks)
        result = cls(value["proxy_id"], value["version"], value["hypothesis_id"],
                     tuple(task_ids), value["rationale"], checks)
        canonical_json(result.to_dict())
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROXY_EVAL_SCHEMA_VERSION,
            "proxy_id": self.proxy_id,
            "version": self.version,
            "hypothesis_id": self.hypothesis_id,
            "task_ids": list(self.task_ids),
            "rationale": self.rationale,
            "checks": [check.to_dict() for check in self.checks],
        }

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def evaluate(self, task_results: list[Mapping[str, Any]]) -> dict[str, Any]:
        by_id = {str(row.get("task_id")): row for row in task_results}
        if not set(self.task_ids) <= set(by_id):
            raise EvalSchemaError("proxy eval lacks development task results")
        selected = [by_id[task_id] for task_id in self.task_ids]
        checks: list[dict[str, Any]] = []
        for check in self.checks:
            numbers = [row.get(check.metric) for row in selected]
            known = all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in numbers)
            observed = round(sum(numbers), 8) if known else None
            passed = (observed <= check.threshold if check.operator == "<=" else observed >= check.threshold) if known else None
            checks.append({**check.to_dict(), "observed": observed, "passed": passed})
        status = "unknown" if any(item["passed"] is None for item in checks) else (
            "passed" if all(item["passed"] for item in checks) else "failed"
        )
        return {"proxy_id": self.proxy_id, "version": self.version, "digest": self.digest,
                "task_count": len(selected), "status": status, "checks": checks,
                "objective_claim": False}


class ProxyEvalRegistry:
    """Immutable agent-authored proposals and explicit local human approval."""

    def __init__(self, state_root: Path):
        self.root = state_root.resolve() / "eval" / "v2" / "proxy-evals"

    def _paths(self, proxy_id: str, version: str) -> tuple[Path, Path]:
        if not _SAFE_ID.fullmatch(proxy_id) or not _SAFE_ID.fullmatch(version):
            raise EvalSchemaError("unsafe proxy eval ID/version")
        folder = self.root / proxy_id
        return folder / f"{version}.json", folder / f"{version}.approval.json"

    def propose(self, proposal: ProxyEvalProposal) -> Path:
        path, _ = self._paths(proposal.proxy_id, proposal.version)
        payload = json.dumps(proposal.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with file_lock(path):
            if path.exists():
                if path.read_text(encoding="utf-8") != payload:
                    raise EvalSchemaError("proxy eval version is immutable; create a new version")
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)
        return path

    def load(self, proxy_id: str, version: str) -> ProxyEvalProposal:
        path, _ = self._paths(proxy_id, version)
        return ProxyEvalProposal.load(path)

    def approve(self, proxy_id: str, version: str, *, reviewer: str) -> dict[str, Any]:
        if not reviewer.strip():
            raise EvalSchemaError("proxy eval approval requires reviewer")
        proposal = self.load(proxy_id, version)
        _, path = self._paths(proxy_id, version)
        with file_lock(path):
            existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if existing is not None:
                if existing.get("proposal_digest") != proposal.digest:
                    raise EvalSchemaError("proxy eval changed after approval")
                return existing
            approval = {"schema_version": "praxile.proxy_eval_approval.v1", "proxy_id": proxy_id,
                        "version": version, "proposal_digest": proposal.digest,
                        "reviewer": reviewer.strip(), "approved_at": utc_now()}
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(path)
            return approval

    def load_approved(self, proxy_id: str, version: str) -> tuple[ProxyEvalProposal, dict[str, Any]]:
        proposal = self.load(proxy_id, version)
        _, path = self._paths(proxy_id, version)
        if not path.is_file():
            raise EvalSchemaError("proxy eval requires explicit human approval")
        approval = json.loads(path.read_text(encoding="utf-8"))
        if (approval.get("schema_version") != "praxile.proxy_eval_approval.v1"
                or approval.get("proxy_id") != proxy_id or approval.get("version") != version
                or approval.get("proposal_digest") != proposal.digest
                or not isinstance(approval.get("reviewer"), str) or not approval["reviewer"].strip()):
            raise EvalSchemaError("proxy eval approval is invalid or digest mismatched")
        return proposal, approval
