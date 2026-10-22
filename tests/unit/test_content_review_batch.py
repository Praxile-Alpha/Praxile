import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from praxile.eval.content_review_batch import review_content_batched


def setup(count=5):
    requirements = [{"id": str(i), "requirement": "Keep this", "source_quote": "source"}
                    for i in range(count)]
    def respond(messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        return {"content": json.dumps({"items": [{"id": r["id"], "verdict": "uncertain",
            "output_quote": "", "reason": "Not enough evidence"} for r in payload["requirements"]]}),
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    router = Mock()
    router.chat.side_effect = respond
    logger = SimpleNamespace(data={}, add_model_cost=Mock())
    return router, logger, requirements


def call(router, logger, requirements, **kwargs):
    return review_content_batched(router, logger, source_text="source", output_text="output",
        requirements=requirements, total_output_tokens=100, batch_size=2, **kwargs)


def test_frozen_partition_budget_and_complete_coverage():
    router, logger, requirements = setup()
    result = call(router, logger, requirements)
    assert result["status"] == "reviewed"
    assert [c.kwargs["max_tokens"] for c in router.chat.call_args_list] == [34, 33, 33]
    assert result["allocated_output_tokens_requested"] == 100
    assert [i["id"] for i in result["items"]] == ["0", "1", "2", "3", "4"]
    assert result["unreviewed_ids"] == []
    assert result["all_declared_requirements_preserved"] is False
    assert result["promotion_eligible"] is False
    assert result["logical_requests"] == logger.add_model_cost.call_count == 3
    assert all(json.loads(c.args[0][1]["content"])["source_text"] == "source"
               for c in router.chat.call_args_list)


def test_failed_group_stops_without_reallocating_or_accepting_partial():
    router, logger, requirements = setup()
    valid = router.chat.side_effect
    count = 0
    def respond(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            return {"content": '{"items": []}', "usage": {"completion_tokens": 30}}
        return valid(*args, **kwargs)
    router.chat.side_effect = respond
    result = call(router, logger, requirements)
    assert result["status"] == "abstained"
    assert result["failed_group_index"] == 1
    assert result["unreviewed_ids"] == ["2", "3", "4"]
    assert result["allocated_output_tokens_requested"] == 67
    assert router.chat.call_count == 2
    assert "items" not in result
    assert len(result["groups"][0]["items"]) == 2


@pytest.mark.parametrize("defect", ["duplicate", "quote"])
def test_all_input_validation_precedes_any_model_call(defect):
    router, logger, requirements = setup()
    requirements[-1]["id" if defect == "duplicate" else "source_quote"] = (
        "0" if defect == "duplicate" else "invented")
    with pytest.raises(ValueError):
        call(router, logger, requirements)
    router.chat.assert_not_called()


def test_checkpoint_binds_group_and_failed_persistence_stops():
    router, logger, requirements = setup()
    saved = []
    def checkpoint(payload):
        saved.append(payload)
        if payload["group_index"] == 1:
            raise OSError("disk unavailable")
    result = call(router, logger, requirements, response_checkpoint=checkpoint)
    assert result["status"] == "abstained"
    assert len(saved) == 2
    assert all(p["batch_plan_sha256"] == result["plan_sha256"] for p in saved)
    assert result["groups"][1]["failure_stage"] == "response_checkpoint"
    assert router.chat.call_count == 2


@pytest.mark.parametrize("budget", [0, True, 1, 24001])
def test_invalid_budget_fails_before_call(budget):
    router, logger, requirements = setup()
    with pytest.raises(ValueError):
        review_content_batched(router, logger, source_text="source", output_text="output",
            requirements=requirements, total_output_tokens=budget, batch_size=2)
    router.chat.assert_not_called()


def test_explicit_allocation_is_frozen_and_changes_plan_identity():
    router, logger, requirements = setup()
    allocations = [20, 30, 50]
    def checkpoint(_):
        allocations[:] = [90, 5, 5]
    result = call(router, logger, requirements, output_allocations=allocations,
                  response_checkpoint=checkpoint)
    assert [c.kwargs["max_tokens"] for c in router.chat.call_args_list] == [20, 30, 50]
    assert [g["max_tokens"] for g in result["plan"]["groups"]] == [20, 30, 50]
    assert result["plan"]["allocation_policy"] == "explicit"
    r2, l2, req2 = setup()
    equal = call(r2, l2, req2)
    assert result["plan_sha256"] != equal["plan_sha256"]


@pytest.mark.parametrize("allocations", [[50, 50], [20, 30, 49], [True, 49, 50],
    [0, 50, 50], [-1, 51, 50], "equal"])
def test_bad_allocation_never_calls_model(allocations):
    router, logger, requirements = setup()
    with pytest.raises(ValueError):
        call(router, logger, requirements, output_allocations=allocations)
    router.chat.assert_not_called()


def test_batch_ledger_excludes_prior_calls_and_retains_failed_unknown():
    from praxile.trajectory import TrajectoryLogger
    from praxile.model import ModelUnavailable
    router, _, requirements = setup()
    valid = router.chat.side_effect
    responses = 0
    def respond(*args, **kwargs):
        nonlocal responses
        responses += 1
        if responses == 2:
            raise ModelUnavailable("unavailable")
        return valid(*args, **kwargs)
    router.chat.side_effect = respond
    logger = TrajectoryLogger("batch", {})
    logger.add_model_cost({"prompt_tokens": 9000, "completion_tokens": 9000})
    result = call(router, logger, requirements)
    evidence = result["usage_evidence"]
    assert evidence["calls"] == 2
    assert evidence["recorded_calls"] == 1
    assert evidence["known_tokens"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert evidence["complete_tokens"] == {"prompt_tokens": None, "completion_tokens": None}
    assert evidence["cost_usd"] is None
    assert evidence["transport_retry_usage_verified"] is False


def test_progress_checkpoint_failure_prevents_model_request():
    router, logger, requirements = setup()
    def fail(_):
        raise OSError("disk unavailable")
    with pytest.raises(OSError):
        call(router, logger, requirements, progress_checkpoint=fail)
    router.chat.assert_not_called()
    record = logger.data["content_review_batches"][0]
    assert record["pending_group_index"] == 0
    assert record["usage_evidence"]["complete_tokens"]["prompt_tokens"] is None


def test_progress_callback_cannot_modify_live_plan_or_results():
    router, logger, requirements = setup()
    saved = []
    def checkpoint(record):
        saved.append(record["status"])
        record["plan"]["groups"].clear()
        record["groups"].clear()
    result = call(router, logger, requirements, progress_checkpoint=checkpoint)
    assert saved[-1] == "reviewed"
    assert len(result["plan"]["groups"]) == len(result["groups"]) == 3
    assert result["pending_group_index"] is None
