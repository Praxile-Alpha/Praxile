"""Input transport admission is explicit, cumulative, and not token accounting."""
from contextlib import closing
import json
import sqlite3

import pytest

from praxile.eval.credential_broker import BrokerDenied, ChatCredentialBroker, SharedChatBudget
from praxile.eval.persistent_chat_budget import PersistentChatBudget


@pytest.fixture(params=["memory", "sqlite"])
def budget(request, tmp_path):
    options = dict(max_calls=10, output_budget=100, ttl_seconds=60, request_byte_budget=500)
    if request.param == "sqlite":
        return PersistentChatBudget.create(tmp_path / "quota.sqlite", **options)
    return SharedChatBudget(**options)


def test_byte_limit_shared_across_roles_without_partial_debits(budget):
    budget.reserve("executor", 10, request_bytes=300)
    before = budget.status()
    with pytest.raises(BrokerDenied):
        budget.reserve("judge", 10, request_bytes=201)
    assert budget.status() == before
    budget.reserve("judge", 10, request_bytes=200)
    assert budget.status()["remaining_request_bytes"] == 0
    assert budget.status()["roles"]["judge"]["reserved_request_bytes"] == 200
    assert budget.status()["total_token_bound_verified"] is False


@pytest.mark.parametrize("value", [None, 0, -1, True, 1.5])
def test_missing_or_invalid_measurements_fail_closed(budget, value):
    before = budget.status()
    with pytest.raises(ValueError):
        budget.reserve("executor", 1, request_bytes=value)
    assert budget.status() == before


def test_broker_measures_final_wire_body_and_keeps_failed_reservation(budget):
    broker = ChatCredentialBroker(key="fixture", endpoint="https://provider.invalid/v1/chat/completions",
        model="fixed", max_calls=10, output_budget=100, max_output=10,
        shared_budget=budget, request_options={"reasoning_effort": "low"})
    bodies = []
    def fail(body):
        bodies.append(body)
        assert budget.status()["remaining_request_bytes"] == 500 - sum(map(len, bodies))
        raise TimeoutError("scripted failure")
    broker._forward = fail
    payload = {"model": "fixed", "messages": [{"role": "user", "content": "\u4e2d\u6587"}]}
    try:
        with pytest.raises(TimeoutError):
            broker.chat("Bearer " + broker.capability, payload)
        assert json.loads(bodies[0])["reasoning_effort"] == "low"
        assert json.loads(bodies[0])["max_tokens"] == 10
        remaining = budget.status()["remaining_request_bytes"]
        budget.reserve("judge", 1, request_bytes=remaining)
        with pytest.raises(BrokerDenied):
            broker.chat("Bearer " + broker.capability, payload)
        assert len(bodies) == 1
        assert broker.status()["usage"]["admitted_calls"] == 1
    finally:
        broker.close()


def test_sqlite_reopen_preserves_byte_balance_and_validates_conservation(tmp_path):
    path = tmp_path / "quota.sqlite"
    budget = PersistentChatBudget.create(path, max_calls=10, output_budget=100,
        ttl_seconds=60, request_byte_budget=500)
    budget.reserve("executor", 10, request_bytes=300)
    restored = PersistentChatBudget(path)
    assert restored.status()["remaining_request_bytes"] == 200
    with pytest.raises(BrokerDenied):
        restored.reserve("judge", 1, request_bytes=201)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            state = json.loads(connection.execute("SELECT state FROM quota").fetchone()[0])
            state["remaining_request_bytes"] = 500
            connection.execute("UPDATE quota SET state=?", (json.dumps(state),))
    with pytest.raises(BrokerDenied, match="conservation"):
        PersistentChatBudget(path)


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, 10**9 + 1])
def test_invalid_byte_limit_does_not_allocate_database(tmp_path, limit):
    path = tmp_path / "quota.sqlite"
    with pytest.raises(ValueError):
        PersistentChatBudget.create(path, max_calls=2, output_budget=20,
            ttl_seconds=60, request_byte_budget=limit)
    assert not path.exists()
