"""Consistency checks only; these do not certify LLM semantic conclusions."""
import json
from pathlib import Path


def test_review_receipt_binds_all_artifacts_and_retains_unknown_usage():
    root = Path(__file__).resolve().parents[2] / "docs/results"
    receipt = json.loads((root / "docx-content-review-20261021.json").read_text())
    execution = json.loads((root / "docx-six-arm-20261020.json").read_text())
    expected = {}
    filename = "FY25_AEG_Social_Justice_Award_Packet_Checklist_DRAFT.docx"
    for pair in execution["pairs"]:
        for arm in pair["arms"]:
            expected[pair["candidate"] + "-" + arm["arm"]] = arm["checks"]["artifacts"][filename]
    assert {arm["arm"]: arm["artifact_sha256"] for arm in receipt["arms"]} == expected
    assert receipt["logical_requests"] == len(receipt["arms"]) == 6
    assert receipt["independent_task_count"] == 1
    assert receipt["official_evaluation"] is False
    assert receipt["promotion_eligible"] is False
    assert receipt["full_token_total"] is None
    assert receipt["cost_usd"] is None
    reviewed = [arm for arm in receipt["arms"] if arm["status"] == "reviewed"]
    assert len(reviewed) == 2
    for arm in reviewed:
        assert sum(arm["verdict_counts"].values()) == receipt["requirements_count"]
        assert len({item["id"] for item in arm["items"]}) == 16
        assert arm["reported_tokens"]["input"] > 0
    for arm in receipt["arms"]:
        assert arm["content_preservation_verified"] is False
        if arm["status"] == "abstained":
            assert arm["failure_stage"] == "request"
            assert arm["reported_tokens"] is None
            assert not arm["items"]
