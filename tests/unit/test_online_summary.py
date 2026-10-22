from copy import deepcopy

import pytest

from praxile.eval import online_summary as module


@pytest.fixture
def setup(monkeypatch):
    status = {"pending_index": None, "completed_task_ids": ["a", "b", "c"], "task_count": 4,
              "ledger_sha256": "f" * 64, "judge_precommitted_before_execution": True}
    class Stream:
        def __init__(self, directory):
            pass

        def status(self):
            return deepcopy(status)
    monkeypatch.setattr(module, "OnlineStream", Stream)
    audits = {
        0: {"status": "complete_judge_labels", "verdict_counts": {"preserved": 1}, "requirement_count": 2},
        1: {"status": "complete_judge_labels", "verdict_counts": {"preserved": 9}, "requirement_count": 9},
        2: {"status": "unknown", "verdict_counts": None, "requirement_count": 1}}
    calls = []
    def audit(stream_directory, index, **kwargs):
        calls.append(index)
        return {"judge_protocol_sha256": "a" * 64, "receipt_sha256": str(index), **audits[index]}
    monkeypatch.setattr(module, "audit_online_review", audit)
    entries = [{"task_index": i, "directory": f"review-{i}", "receipt_sha256": str(i), "judge": {}}
               for i in range(3)]
    return status, audits, calls, entries


def test_task_weighting_unknowns_and_order(setup):
    _, _, calls, entries = setup
    result = module.summarize_online_stream("stream", list(reversed(entries)))
    assert calls == [2, 1, 0]
    assert [row["task_index"] for row in result["rows"]] == [0, 1, 2, 3]
    assert result["observed_task_mean"] == 0.75
    assert result["planned_task_mean_bounds"] == {"lower": 0.375, "upper": 0.875}
    assert result["complete_label_coverage"] == 0.5
    assert result["unknown_or_unexecuted_tasks"] == 2
    assert result["rows"][3]["status"] == "not_executed"
    assert result["causal_improvement_estimated"] is False


def test_empty_reviews_preserve_denominator(setup):
    result = module.summarize_online_stream("stream", [])
    assert result["observed_task_mean"] is None
    assert result["planned_task_mean_bounds"] == {"lower": 0, "upper": 1}
    assert result["rows"][0]["status"] == "missing_review"


@pytest.mark.parametrize("defect", ["duplicate", "future", "bool", "mixed_judge", "pending", "changed"])
def test_invalid_or_unstable_summary_rejected(setup, defect, monkeypatch):
    status, audits, _, entries = setup
    if defect == "duplicate":
        entries.append(entries[0])
    elif defect == "future":
        entries[0]["task_index"] = 3
    elif defect == "bool":
        entries[0]["task_index"] = True
    elif defect == "mixed_judge":
        audits[1]["judge_protocol_sha256"] = "b" * 64
    elif defect == "pending":
        status["pending_index"] = 3
    else:
        original = module.audit_online_review
        def changed(*args, **kwargs):
            status["ledger_sha256"] = "changed"
            return original(*args, **kwargs)
        monkeypatch.setattr(module, "audit_online_review", changed)
    with pytest.raises(ValueError):
        module.summarize_online_stream("stream", entries)
