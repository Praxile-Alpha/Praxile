from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ...trace import AgentEvent, ArtifactRecord
from ...utils import utc_now
from .schema import EvalSchemaError, canonical_json


DIAGNOSIS_SCHEMA_VERSION = "praxile.failure_diagnosis.v1"
FAILURE_CATEGORIES = (
    "MODEL",
    "CONTEXT",
    "TOOL",
    "ENVIRONMENT",
    "VERIFICATION",
    "POLICY_HARNESS",
    "UNKNOWN",
)


@dataclass(frozen=True)
class FailureDetection:
    category: str
    code: str
    summary: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _category(self.category)
        _non_empty(self.code, "detection code")
        _non_empty(self.summary, "detection summary")
        _evidence_refs(self.evidence_refs)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureDetection":
        return cls(
            category=str(value.get("category") or ""),
            code=str(value.get("code") or ""),
            summary=str(value.get("summary") or ""),
            evidence_refs=_strings(value.get("evidence_refs"), "detection evidence_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "code": self.code,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class CausalAttribution:
    category: str
    confidence: float
    abstained: bool
    rationale: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _category(self.category)
        if not 0.0 <= self.confidence <= 1.0:
            raise EvalSchemaError("attribution confidence must be between 0 and 1")
        if self.abstained and self.category != "UNKNOWN":
            raise EvalSchemaError("abstained attribution must use category UNKNOWN")
        if not self.abstained and self.category == "UNKNOWN":
            raise EvalSchemaError("UNKNOWN attribution must abstain")
        _non_empty(self.rationale, "attribution rationale")
        if self.evidence_refs:
            _evidence_refs(self.evidence_refs)
        elif not self.abstained:
            raise EvalSchemaError("a non-abstained attribution requires evidence_refs")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CausalAttribution":
        return cls(
            category=str(value.get("category") or ""),
            confidence=float(value.get("confidence", 0.0)),
            abstained=bool(value.get("abstained")),
            rationale=str(value.get("rationale") or ""),
            evidence_refs=_strings(value.get("evidence_refs"), "attribution evidence_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "abstained": self.abstained,
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class FailureDiagnosis:
    diagnosis_id: str
    task_id: str
    trace_id: str
    run_id: str
    outcome: str
    detections: tuple[FailureDetection, ...]
    attribution: CausalAttribution
    created_at: str = field(default_factory=utc_now)
    schema_version: str = DIAGNOSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DIAGNOSIS_SCHEMA_VERSION:
            raise EvalSchemaError(f"unsupported diagnosis schema: {self.schema_version}")
        for name in ("diagnosis_id", "task_id", "trace_id", "run_id", "outcome", "created_at"):
            _non_empty(getattr(self, name), name)
        if self.outcome not in {"failure", "no_failure", "unknown"}:
            raise EvalSchemaError(f"unsupported diagnosis outcome: {self.outcome}")
        canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureDiagnosis":
        detections = value.get("detections")
        attribution = value.get("attribution")
        if not isinstance(detections, list) or not isinstance(attribution, Mapping):
            raise EvalSchemaError("diagnosis detections must be an array and attribution an object")
        return cls(
            schema_version=str(value.get("schema_version") or ""),
            diagnosis_id=str(value.get("diagnosis_id") or ""),
            task_id=str(value.get("task_id") or ""),
            trace_id=str(value.get("trace_id") or ""),
            run_id=str(value.get("run_id") or ""),
            outcome=str(value.get("outcome") or ""),
            detections=tuple(FailureDetection.from_dict(item) for item in detections),
            attribution=CausalAttribution.from_dict(attribution),
            created_at=str(value.get("created_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "diagnosis_id": self.diagnosis_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "detections": [item.to_dict() for item in self.detections],
            "attribution": self.attribution.to_dict(),
            "created_at": self.created_at,
        }


class FailureDiagnoser:
    """Evidence-first P0 classifier; it detects facts and abstains on ambiguous cause."""

    def diagnose(
        self,
        task_result: Mapping[str, Any],
        events: Iterable[AgentEvent],
        artifacts: Iterable[ArtifactRecord] = (),
    ) -> FailureDiagnosis:
        rows = list(events)
        artifact_rows = list(artifacts)
        trace_id = str(task_result.get("trace_id") or (rows[0].trace_id if rows else ""))
        run_id = str(task_result.get("agent_run_id") or (rows[0].run_id if rows else ""))
        task_id = str(task_result.get("task_id") or (rows[0].task_id if rows else ""))
        if not all((trace_id, run_id, task_id)):
            raise EvalSchemaError("diagnosis requires task, trace, and run identity")
        if any(event.trace_id != trace_id or event.task_id != task_id for event in rows):
            raise EvalSchemaError("diagnosis events do not belong to the requested trace/task")
        if any(artifact.trace_id != trace_id for artifact in artifact_rows):
            raise EvalSchemaError("diagnosis artifacts do not belong to the requested trace")

        detections = self._detections(task_result, rows, artifact_rows)
        evaluator = task_result.get("evaluator") if isinstance(task_result.get("evaluator"), Mapping) else {}
        resolved = evaluator.get("resolved")
        if resolved is True:
            outcome = "no_failure"
        elif resolved is False or str(task_result.get("status") or "") == "error":
            outcome = "failure"
        else:
            outcome = "unknown"
        attribution = self._attribute(outcome, detections)
        identity = canonical_json(
            {
                "schema_version": DIAGNOSIS_SCHEMA_VERSION,
                "task_id": task_id,
                "trace_id": trace_id,
                "run_id": run_id,
                "outcome": outcome,
                "detections": [item.to_dict() for item in detections],
                "attribution": attribution.to_dict(),
            }
        ).encode("utf-8")
        return FailureDiagnosis(
            diagnosis_id="diagnosis_" + hashlib.sha256(identity).hexdigest()[:16],
            task_id=task_id,
            trace_id=trace_id,
            run_id=run_id,
            outcome=outcome,
            detections=tuple(detections),
            attribution=attribution,
        )

    def _detections(
        self,
        task_result: Mapping[str, Any],
        events: list[AgentEvent],
        artifacts: list[ArtifactRecord],
    ) -> list[FailureDetection]:
        found: list[FailureDetection] = []
        for event in events:
            refs = _event_refs(event)
            payload = event.payload
            status = str(payload.get("status") or "").lower()
            if event.type == "MODEL_CALL" and (
                payload.get("parse_status") == "rejected" or payload.get("error") or status in {"failed", "error"}
            ):
                found.append(FailureDetection("MODEL", "model_call_rejected", "A model response was rejected or failed.", refs))
            if event.type == "CONTEXT_INJECT" and (
                payload.get("truncated") or payload.get("missing") or status in {"failed", "rejected", "missing"}
            ):
                found.append(FailureDetection("CONTEXT", "context_delivery_degraded", "Injected context was missing, truncated, or rejected.", refs))
            if event.type == "PREFLIGHT" and status == "failed":
                found.append(
                    FailureDetection(
                        "ENVIRONMENT",
                        "workspace_preflight_failed",
                        "The adapter rejected the execution workspace before launching the agent.",
                        refs,
                    )
                )
            if event.type == "TOOL_RESULT":
                returncode = payload.get("returncode")
                failed = status in {"failed", "error", "timeout", "timed_out"} or (
                    isinstance(returncode, int) and not isinstance(returncode, bool) and returncode != 0
                )
                if failed:
                    category = "ENVIRONMENT" if status in {"timeout", "timed_out"} or payload.get("exception") else "TOOL"
                    code = "environment_execution_failure" if category == "ENVIRONMENT" else "tool_result_failed"
                    found.append(FailureDetection(category, code, "A tool execution did not complete successfully.", refs))
            if event.type == "VERIFICATION" and (payload.get("resolved") is False or status in {"failed", "error"}):
                found.append(FailureDetection("VERIFICATION", "verification_failed", "Objective verification reported failure.", refs))
            if event.type in {"FINAL_RESULT", "RUN_END", "CHECKPOINT"} and (
                status in {"blocked", "policy_blocked", "safety_blocked"} or payload.get("policy_blocked")
            ):
                found.append(FailureDetection("POLICY_HARNESS", "policy_blocked", "Harness policy blocked execution or completion.", refs))
            native_exit = str(payload.get("native_exit_status") or "").lower()
            if event.type == "FINAL_RESULT" and native_exit in {
                "limitsexceeded",
                "steplimitexceeded",
                "costlimitexceeded",
                "timeexceeded",
            }:
                found.append(
                    FailureDetection(
                        "POLICY_HARNESS",
                        "execution_budget_exhausted",
                        "The configured execution budget ended the agent before completion.",
                        refs,
                    )
                )

        evaluator = task_result.get("evaluator") if isinstance(task_result.get("evaluator"), Mapping) else {}
        if evaluator.get("resolved") is False and not any(item.category == "VERIFICATION" for item in found):
            refs = tuple(f"artifact:{item.artifact_id}" for item in artifacts if item.type.startswith("evaluator_"))
            if not refs:
                refs = _terminal_event_refs(events)
            if refs:
                found.append(FailureDetection("VERIFICATION", "benchmark_unresolved", "The benchmark evaluator marked the patch unresolved.", refs))
        error = task_result.get("error") if isinstance(task_result.get("error"), Mapping) else {}
        if error and not found:
            refs = _terminal_event_refs(events)
            if refs:
                found.append(FailureDetection("UNKNOWN", "unclassified_run_error", "The run failed without category-specific evidence.", refs))
        return _deduplicate(found)

    @staticmethod
    def _attribute(outcome: str, detections: list[FailureDetection]) -> CausalAttribution:
        if outcome == "no_failure":
            return CausalAttribution(
                "UNKNOWN", 0.0, True, "The task was resolved; no failure cause is attributed.", ()
            )
        direct = {item.category for item in detections if item.category != "UNKNOWN"}
        if outcome == "failure" and len(direct) == 1:
            category = next(iter(direct))
            refs = tuple(dict.fromkeys(ref for item in detections if item.category == category for ref in item.evidence_refs))
            return CausalAttribution(
                category,
                0.8,
                False,
                "A single directly observed failure category is supported by normalized evidence.",
                refs,
            )
        reason = (
            "Multiple observed categories prevent a defensible single-cause attribution."
            if len(direct) > 1
            else "Available evidence is insufficient for causal attribution."
        )
        refs = tuple(dict.fromkeys(ref for item in detections for ref in item.evidence_refs))
        return CausalAttribution("UNKNOWN", 0.0, True, reason, refs)


def _event_refs(event: AgentEvent) -> tuple[str, ...]:
    return (f"event:{event.event_id}", *(f"artifact:{item}" for item in event.artifact_ids))


def _terminal_event_refs(events: Iterable[AgentEvent]) -> tuple[str, ...]:
    return tuple(f"event:{event.event_id}" for event in events if event.type in {"FINAL_RESULT", "RUN_END"})


def _deduplicate(items: list[FailureDetection]) -> list[FailureDetection]:
    result: list[FailureDetection] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in items:
        key = (item.category, item.code, item.evidence_refs)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _category(value: str) -> None:
    if value not in FAILURE_CATEGORIES:
        raise EvalSchemaError(f"unsupported failure category: {value!r}")


def _non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise EvalSchemaError(f"{name} must be a non-empty string")


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) or not item for item in value):
        raise EvalSchemaError(f"{name} must be an array of non-empty strings")
    return tuple(value)


def _evidence_refs(value: tuple[str, ...]) -> None:
    if not value:
        raise EvalSchemaError("a failure detection or attributed cause requires evidence_refs")
    for item in value:
        if not item.startswith(("event:", "artifact:")):
            raise EvalSchemaError(f"unsupported evidence reference: {item!r}")
