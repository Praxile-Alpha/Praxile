from concurrent.futures import ThreadPoolExecutor

import pytest

from praxile.eval.credential_broker import BrokerDenied, ChatCredentialBroker, SharedChatBudget


def broker(budget, role):
    value = ChatCredentialBroker(key="fixture", endpoint="https://provider.invalid/v1/chat/completions",
        model="fixed", max_calls=10, output_budget=100, max_output=10,
        shared_budget=budget, budget_role=role)
    value._forward = lambda body: {"choices": []}
    return value


def request(value):
    return value.chat("Bearer " + value.capability,
        {"model": "fixed", "messages": [{"role": "user", "content": "test"}]})


def test_roles_share_one_budget_and_new_broker_does_not_reset_it():
    budget = SharedChatBudget(max_calls=2, output_budget=20, ttl_seconds=10)
    with broker(budget, "executor") as executor, broker(budget, "judge") as judge:
        request(executor)
        request(judge)
        with broker(budget, "composer") as composer:
            with pytest.raises(BrokerDenied, match="shared budget exhausted"):
                request(composer)
            assert composer.status()["usage"]["admitted_calls"] == 0
        assert executor.status()["remaining_calls"] == 9
    status = budget.status()
    assert status["remaining_calls"] == status["remaining_output"] == 0
    assert status["roles"] == {role: {"reserved_calls": 1, "reserved_output": 10}
                               for role in ("executor", "judge")}
    assert status["total_token_bound_verified"] is False


def test_failed_call_keeps_shared_reservation():
    budget = SharedChatBudget(max_calls=2, output_budget=10, ttl_seconds=10)
    with broker(budget, "executor") as executor:
        def fail(body):
            raise TimeoutError("fixture")
        executor._forward = fail
        with pytest.raises(TimeoutError):
            request(executor)
        with pytest.raises(BrokerDenied):
            request(executor)
        assert executor.status()["usage"]["failed_calls"] == 1
    assert budget.status()["remaining_calls"] == 1
    assert budget.status()["remaining_output"] == 0


def test_expiry_and_invalid_auth_never_forward(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("praxile.eval.credential_broker.time.monotonic", lambda: now[0])
    budget = SharedChatBudget(max_calls=2, output_budget=20, ttl_seconds=1)
    with broker(budget, "judge") as value:
        with pytest.raises(BrokerDenied):
            value.chat("invalid", {})
        assert budget.status()["remaining_calls"] == 2
        now[0] = 2.0
        with pytest.raises(BrokerDenied, match="shared budget expired"):
            request(value)
        assert value.status()["usage"]["admitted_calls"] == 0


@pytest.mark.resource
def test_concurrent_brokers_cannot_exceed_shared_call_cap():
    budget = SharedChatBudget(max_calls=1, output_budget=20, ttl_seconds=10)
    def attempt(role):
        with broker(budget, role) as value:
            try:
                request(value)
                return True
            except BrokerDenied:
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, ("executor", "judge"))) == 1
    assert budget.status()["remaining_calls"] == 0


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_shared_limits_rejected(value):
    with pytest.raises(ValueError):
        SharedChatBudget(max_calls=value, output_budget=20, ttl_seconds=10)
