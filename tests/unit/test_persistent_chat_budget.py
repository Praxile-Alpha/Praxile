import json
from contextlib import closing
import sqlite3
import subprocess
import sys

import pytest

from praxile.eval.credential_broker import BrokerDenied, ChatCredentialBroker
from praxile.eval.persistent_chat_budget import PersistentChatBudget


def create(tmp_path, calls=2):
    return PersistentChatBudget.create(tmp_path / "quota.sqlite", max_calls=calls,
                                      output_budget=20, ttl_seconds=60)


def test_reopening_does_not_restore_spent_quota(tmp_path):
    budget = create(tmp_path)
    budget.reserve("executor", 10)
    restored = PersistentChatBudget(tmp_path / "quota.sqlite")
    restored.reserve("judge", 10)
    with pytest.raises(BrokerDenied):
        budget.reserve("composer", 1)
    assert restored.status()["remaining_calls"] == 0
    assert restored.status()["remaining_output"] == 0
    with pytest.raises(FileExistsError):
        create(tmp_path)


def test_broker_failure_does_not_refund_persistent_reservation(tmp_path):
    budget = create(tmp_path, calls=1)
    with ChatCredentialBroker(key="fixture", endpoint="https://provider.invalid/v1/chat/completions",
            model="fixed", max_calls=2, output_budget=20, max_output=10, shared_budget=budget) as broker:
        def fail(body):
            assert PersistentChatBudget(tmp_path / "quota.sqlite").status()["remaining_calls"] == 0
            raise TimeoutError("fixture")
        broker._forward = fail
        with pytest.raises(TimeoutError):
            broker.chat("Bearer " + broker.capability, {"model": "fixed",
                "messages": [{"role": "user", "content": "fixture"}]})
    assert PersistentChatBudget(tmp_path / "quota.sqlite").status()["remaining_output"] == 10


def test_missing_database_is_not_recreated(tmp_path):
    budget = create(tmp_path)
    (tmp_path / "quota.sqlite").unlink()
    with pytest.raises(BrokerDenied):
        budget.reserve("executor", 1)
    assert not (tmp_path / "quota.sqlite").exists()


def test_quota_cli_create_show_and_duplicate_refusal(tmp_path, capsys):
    from praxile.eval.persistent_chat_budget import main
    path = tmp_path / "quota.sqlite"
    args = ["create", "--database", str(path), "--max-calls", "2", "--output-budget", "20", "--ttl-seconds", "60"]
    assert main(args) == 0
    before = json.loads(capsys.readouterr().out)
    assert main(["show", "--database", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == before
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert PersistentChatBudget(path).status() == before


def test_invalid_balance_fails_closed(tmp_path):
    budget = create(tmp_path)
    with closing(sqlite3.connect(tmp_path / "quota.sqlite")) as connection:
        with connection:
            state = json.loads(connection.execute("SELECT state FROM quota").fetchone()[0])
            state["remaining_calls"] = 1
            connection.execute("UPDATE quota SET state=?", (json.dumps(state),))
    with pytest.raises(BrokerDenied, match="conservation"):
        budget.reserve("judge", 1)


@pytest.mark.parametrize("now", [90, 200])
def test_clock_rollback_and_expiry_fail_closed(tmp_path, monkeypatch, now):
    monkeypatch.setattr("praxile.eval.persistent_chat_budget.time.time", lambda: 100)
    budget = create(tmp_path)
    monkeypatch.setattr("praxile.eval.persistent_chat_budget.time.time", lambda: now)
    with pytest.raises(BrokerDenied, match="clock|expired"):
        budget.reserve("judge", 1)
    assert budget.status()["remaining_calls"] == 2


@pytest.mark.resource
def test_two_processes_share_one_committed_call(tmp_path):
    create(tmp_path, calls=1)
    script = """
import sys
from praxile.eval.persistent_chat_budget import PersistentChatBudget
from praxile.eval.credential_broker import BrokerDenied
try:
    PersistentChatBudget(sys.argv[1]).reserve(sys.argv[2], 1)
except BrokerDenied:
    raise SystemExit(2)
"""
    processes = []
    try:
        for role in ("executor", "judge"):
            processes.append(subprocess.Popen([sys.executable, "-c", script, str(tmp_path / "quota.sqlite"), role],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for process in processes:
            output, error = process.communicate(timeout=10)
            assert not error, error.decode()
        assert sorted(process.returncode for process in processes) == [0, 2]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
    assert PersistentChatBudget(tmp_path / "quota.sqlite").status()["remaining_calls"] == 0
