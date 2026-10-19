"""Offline replay of published model drafts; no provider, tools or evaluator runs."""
import json
from pathlib import Path

import pytest

from praxile.eval.native_context import plan_candidate
from praxile.eval.v2.candidate import ContextCandidate
from praxile.eval.v2.executor_profile import common_parent_capability


ROOT = Path(__file__).resolve().parents[2] / "docs/results/docx-typed-actions-20261019"


def load(name):
    return json.loads((ROOT / name).read_text())


@pytest.mark.parametrize("index,name", list(enumerate(["first.json", "second.json", "composition.json"])))
def test_published_candidates_preserve_identity_references_and_validation(index, name):
    receipt, evidence = load("receipt.json"), load("evidence.json")
    candidate = ContextCandidate.from_dict(load(name))
    assert candidate.digest == receipt["design"]["candidate_digests"][index]
    assert candidate.confidence == 0
    allowed = {"event:" + item["event_id"] for item in evidence["evidence"]}
    assert set(candidate.evidence_refs) <= allowed
    assert set(candidate.source_task_ids) == {evidence["task_id"]}
    common_parent_capability([candidate])
    requirements = candidate.context_item["executor_requirements"]
    assert requirements["schema_validated"] is True
    assert requirements["execution_verified"] is False
    assert requirements["permission_granted"] is False


@pytest.mark.parametrize("name", ["first.json", "second.json", "composition.json"])
def test_published_candidates_activate_only_in_declared_scope(name):
    candidate = load(name)
    context = load("evidence.json")["public_task_context"]
    instruction = context["goal"] + " " + " ".join(context["constraints"])
    matching = plan_candidate(candidate, instruction, "jobbench/easy-technical-writers", development_only=True)
    assert matching["activation"]["activated"] is True
    assert matching["permission_granted"] is False
    assert matching["promotion_eligible"] is False
    unrelated = plan_candidate(candidate, instruction, "different/project", development_only=True)
    assert unrelated["activation"]["activated"] is False
    assert unrelated["content"] is None
    excluded = plan_candidate(candidate, "Create a multi-page document, not a one-page checklist.",
                              "jobbench/easy-technical-writers", development_only=True)
    assert excluded["activation"]["activated"] is False


def test_published_receipt_does_not_claim_quality_or_execution():
    receipt = load("receipt.json")
    assert receipt["candidate_execution_started"] is False
    assert receipt["promotion_eligible"] is False
    assert receipt["quality_review"]["content_preservation_verified"] is False
    assert receipt["quality_review"]["causal_attribution_verified"] is False
    evidence = load("evidence.json")
    assert evidence["provenance_attested"] is False
    assert len(evidence["evidence"]) == 10
    assert receipt["cost"]["model_calls"] == 1
