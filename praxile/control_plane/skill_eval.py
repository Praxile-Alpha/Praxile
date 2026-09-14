from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .assets import SkillAsset
from .common import ControlPlaneSchemaError, EvidenceRef, evidence_refs, require_mapping, strict_bool


SKILL_EVALUATION_SCHEMA_VERSION = "praxile.skill_evaluation.v1"


@dataclass(frozen=True)
class SkillCaseResult:
    eval_case_id: str
    passed: bool
    evidence: tuple[EvidenceRef, ...]
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.eval_case_id:
            raise ControlPlaneSchemaError("eval_case_id must be non-empty")
        strict_bool(self.passed, "skill case passed")
        evidence_refs(self.evidence)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SkillCaseResult":
        return cls(
            eval_case_id=str(value.get("eval_case_id") or ""),
            passed=strict_bool(value.get("passed"), "skill case passed"),
            evidence=evidence_refs(value.get("evidence", [])),
            notes=str(value.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SKILL_EVALUATION_SCHEMA_VERSION,
            "eval_case_id": self.eval_case_id,
            "passed": self.passed,
            "evidence": [item.to_dict() for item in self.evidence],
            "notes": self.notes,
        }


class SkillAssetEvaluator:
    """Evaluate a Skill Asset against every declared eval case."""

    def evaluate(self, skill: SkillAsset, results: Mapping[str, SkillCaseResult | Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(results, Mapping):
            raise ControlPlaneSchemaError("skill case results must be an object")
        normalized = {
            case_id: value if isinstance(value, SkillCaseResult) else SkillCaseResult.from_dict(value)
            for case_id, value in results.items()
        }
        mismatched = sorted(case_id for case_id, result in normalized.items() if case_id != result.eval_case_id)
        if mismatched:
            raise ControlPlaneSchemaError(f"skill case result keys do not match eval_case_id: {mismatched}")
        expected = set(skill.eval_cases)
        missing = sorted(expected - set(normalized))
        unexpected = sorted(set(normalized) - expected)
        passed = sorted(case_id for case_id in expected if case_id in normalized and normalized[case_id].passed)
        failed = sorted(case_id for case_id in expected if case_id in normalized and not normalized[case_id].passed)
        eligible = not missing and not unexpected and not failed
        return {
            "schema_version": SKILL_EVALUATION_SCHEMA_VERSION,
            "asset_id": skill.meta.asset_id,
            "asset_version": skill.meta.version,
            "eligible_for_promotion": eligible,
            "summary": {
                "declared": len(expected),
                "passed": len(passed),
                "failed": len(failed),
                "missing": len(missing),
                "unexpected": len(unexpected),
            },
            "passed": passed,
            "failed": failed,
            "missing": missing,
            "unexpected": unexpected,
            "results": [normalized[key].to_dict() for key in sorted(normalized)],
        }


class SkillMarkdownProjector:
    """Create a deterministic human-review projection; JSON remains authoritative."""

    @staticmethod
    def render(skill: SkillAsset, evaluation: Mapping[str, Any] | None = None) -> str:
        sections = [
            f"# {skill.name}",
            "",
            f"- Asset: `{skill.meta.asset_id}`",
            f"- Version: `{skill.meta.version}`",
            f"- Status: `{skill.meta.status}`",
            f"- Confidence: `{skill.meta.confidence:.2f}`",
            f"- Created from run: `{skill.meta.created_from_run}`",
            "",
            "## Applies When",
            "",
            *_bullets(skill.meta.applicable_conditions),
            "",
            "## Preconditions",
            "",
            *_bullets(skill.preconditions),
            "",
            "## Context Requirements",
            "",
            *_bullets(skill.context_requirements),
            "",
            "## Allowed Tools",
            "",
            *_bullets(skill.allowed_tools, code=True),
            "",
            "## Procedure",
            "",
            *[f"{index}. {item}" for index, item in enumerate(skill.procedure, 1)],
            "",
            "## Verification Contract",
            "",
            *_bullets(skill.verification_contract),
            "",
            "## Failure Modes",
            "",
            *_bullets(skill.failure_modes),
            "",
            "## Eval Cases",
            "",
            *_bullets(skill.eval_cases, code=True),
            "",
            "## Input Schema",
            "",
            "```json",
            json.dumps(dict(skill.input_schema), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Source Evidence",
            "",
            *_bullets(tuple(item.canonical_ref for item in skill.meta.source_evidence), code=True),
        ]
        if evaluation is not None:
            require_mapping(evaluation, "skill evaluation")
            sections.extend(
                [
                    "",
                    "## Evaluation",
                    "",
                    "```json",
                    json.dumps(dict(evaluation), ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                ]
            )
        return "\n".join(sections).rstrip() + "\n"


def _bullets(values: tuple[str, ...], *, code: bool = False) -> list[str]:
    return [f"- `{{value}}`".format(value=value) if code else f"- {value}" for value in values]
