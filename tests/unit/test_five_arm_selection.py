import hashlib
import json

import pytest

from praxile.eval.five_arm_selection import select_delivered, selection_policy, validate_selection


def receipts(root, delivered):
    attempts = []
    for index, complete in enumerate(delivered):
        directory = root / f"attempt-{index}"
        directory.mkdir()
        path = directory / "execution.json"
        path.write_text(json.dumps({"status": "executor_returned", "cleanup_verified": True,
            "required_outputs_collected": complete, "hidden_score": 100 - index}))
        attempts.append({"index": index, "status": "returned", "report": path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return attempts


@pytest.mark.parametrize("strategy,expected", [("first_delivered", 1), ("last_delivered", 2)])
def test_selection_uses_delivery_and_order_not_scores(tmp_path, strategy, expected):
    attempts = receipts(tmp_path, [False, True, True])
    report = select_delivered(attempts, tmp_path, selection_policy(strategy))
    assert report["eligible_attempts"] == [1, 2]
    assert report["selected_attempt"] == expected
    assert report["quality_verified"] is False
    assert "hidden_score" not in json.dumps(report)


def test_no_delivery_abstains_instead_of_selecting_failed_output(tmp_path):
    report = select_delivered(receipts(tmp_path, [False, None, 1]), tmp_path, selection_policy("last_delivered"))
    assert report["selected_attempt"] is None
    assert report["reason"] == "no_delivered_attempt"


@pytest.mark.parametrize("defect", ["hash", "link", "escape", "wrong_attempt", "order", "uncertain"])
def test_selection_rejects_untrustworthy_receipts(tmp_path, defect):
    attempts = receipts(tmp_path, [True])
    if defect == "hash":
        (tmp_path / attempts[0]["report"]).write_text("{}")
    elif defect == "link":
        path = tmp_path / attempts[0]["report"]
        saved = tmp_path / "saved"
        path.rename(saved)
        path.symlink_to(saved)
    elif defect == "escape":
        attempts[0]["report"] = "attempt-0/../../outside"
    elif defect == "wrong_attempt":
        attempts[0]["report"] = "attempt-1/execution.json"
    elif defect == "order":
        attempts[0]["index"] = 1
    else:
        attempts[0]["status"] = "outcome_unknown"
    with pytest.raises(ValueError):
        select_delivered(attempts, tmp_path, selection_policy("last_delivered"))


@pytest.mark.parametrize("policy", [None, {}, {"strategy": []}, selection_policy("last_delivered")])
def test_selection_must_match_frozen_identity(policy):
    with pytest.raises(ValueError):
        validate_selection(policy, {"spec": {"identities": {"terminal_selection": "sha256:" + "a" * 64}}})
