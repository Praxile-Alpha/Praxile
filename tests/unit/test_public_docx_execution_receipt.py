"""Check published receipt consistency, not re-execute or certify the experiment."""
import hashlib
import json
from pathlib import Path

from praxile.eval.v2.candidate import ContextCandidate


ROOT = Path(__file__).resolve().parents[2] / "docs/results"


def test_execution_receipt_preserves_scope_and_failed_arms():
    data = json.loads((ROOT / "docx-six-arm-20261020.json").read_text())
    assert data["independent_task_count"] == 1
    assert data["pair_count"] == len(data["pairs"]) == 3
    assert data["official_evaluation"] == "not_run"
    assert data["content_preservation_verified"] is False
    assert data["promotion_eligible"] is False
    assert data["total_cost_usd"] is None
    assert data["cost_scope"] == "execution_only_excludes_design_search_judge"
    frozen = []
    pages = []
    for pair in data["pairs"]:
        manifest = pair["manifest"]
        asset = ROOT / "docx-typed-actions-20261019" / (pair["candidate"] + ".json")
        assert hashlib.sha256(asset.read_bytes()).hexdigest() == manifest["candidate_file_sha256"]
        candidate = ContextCandidate.from_dict(json.loads(asset.read_text()))
        frozen.append({key: manifest[key] for key in (
            "image", "wheel_sha256", "dependency_sha256", "seed_sha256",
            "instruction_sha256", "model", "model_request_options",
            "max_steps_per_arm", "timeout_seconds_per_arm", "public_contract",
        )})
        assert manifest["model"] == "MiniMax-M3.1-Flash"
        assert manifest["model_request_options"] == {"reasoning_effort": "low"}
        assert [arm["arm"] for arm in pair["arms"]] == ["baseline", "candidate"]
        for arm in pair["arms"]:
            checks = arm["checks"]
            assert checks["before"]["pages"] == 5
            assert checks["after"]["memo_unchanged"] is True
            assert checks["after"]["content_preservation_verified"] is False
            assert checks["seed_unchanged"] and checks["environment_stop_returned"]
            assert arm["broker_revoked"] is True
            assert arm["broker_failed_calls"] == arm["broker_inflight_calls"] == 0
            assert arm["input_tokens"] > 0 and arm["output_tokens"] > 0
            if arm["arm"] == "candidate":
                context = arm["development_context"]
                assert context["candidate_digest"] == candidate.digest
                assert context["injected"] and context["activation"]["activated"]
                assert context["promotion_eligible"] is False
            else:
                assert not arm["development_context"]
        pages.append([arm["checks"]["after"]["pages"] for arm in pair["arms"]])
    assert all(item == frozen[0] for item in frozen)
    assert pages == [[1, 1], [1, 1], [5, 1]]
    assert data["pairs"][2]["arms"][0]["runtime_status"] == "needs_human"
