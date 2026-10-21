import asyncio
import json
import os
from unittest.mock import AsyncMock

import pytest

from praxile.eval import lease_recover
from praxile.eval.lease import ExecutionResult
from praxile.eval.owner_lock import OwnerLock

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX ownership locks")


def setup(tmp_path, monkeypatch, *, wrong_label=False, wrong_daemon=False):
    root = tmp_path / ("harbor-lease-" + "a" * 32)
    root.mkdir()
    session = "praxile-lease-" + "b" * 32
    with OwnerLock(root / "owner.lock", create=True) as owner:
        identity = owner.identity
    record = {"schema_version": "praxile.harbor_ownership.v1", "lease_id": root.name,
              "owner_pid": os.getpid(), "owner_lock": identity,
              "generations": [{"session": session, "daemon_id": "fixture", "phase": "ready"}]}
    (root / "ownership.json").write_text(json.dumps(record))
    observed = {"generations": [{"session": session, "observation": "daemon_mismatch" if wrong_daemon else "present",
                "resources": {"container": ["fixture-id"], "network": [], "volume": []}}], "resources_absent": False}
    monkeypatch.setattr(lease_recover, "inspect_lease", AsyncMock(side_effect=[observed, {"resources_absent": True}]))
    commands = []
    class Host:
        def __init__(self, root):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, argv, *, timeout):
            commands.append(argv)
            if argv[1] == "info":
                value = "fixture"
            elif argv[2] == "inspect":
                value = json.dumps({"com.docker.compose.project": "other" if wrong_label else session})
            else:
                assert argv == ["docker", "container", "rm", "-f", "fixture-id"]
                value = "fixture-id"
            return ExecutionResult(0, value, "")
    monkeypatch.setattr(lease_recover, "LocalWorkspaceLease", Host)
    return root, session, commands


def test_guarded_cleanup_records_exact_removals(tmp_path, monkeypatch):
    root, session, commands = setup(tmp_path, monkeypatch)
    before = (root / "ownership.json").read_bytes()
    path = asyncio.run(lease_recover.recover_lease(root, confirm_sessions=[session]))
    receipt = json.loads(path.read_text())
    assert receipt["status"] == "cleaned" and receipt["resources_absent"] is True
    assert receipt["removed"] == [{"kind": "container", "id": "fixture-id", "session": session}]
    assert (root / "ownership.json").read_bytes() == before
    assert len(commands) == 3
    with OwnerLock(root / "owner.lock"):
        pass


@pytest.mark.parametrize("condition", ["active", "confirmation", "replacement", "legacy"])
def test_recovery_refuses_before_docker_queries(tmp_path, monkeypatch, condition):
    root, session, commands = setup(tmp_path, monkeypatch)
    owner = None
    confirmations = [session]
    if condition == "active":
        owner = OwnerLock(root / "owner.lock")
    elif condition == "confirmation":
        confirmations = []
    elif condition == "replacement":
        (root / "owner.lock").rename(root / "old.lock")
        OwnerLock(root / "owner.lock", create=True).close()
    else:
        path = root / "ownership.json"
        record = json.loads(path.read_text())
        del record["owner_lock"]
        path.write_text(json.dumps(record))
    try:
        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(lease_recover.recover_lease(root, confirm_sessions=confirmations))
        assert commands == []
        lease_recover.inspect_lease.assert_not_called()
    finally:
        if owner:
            owner.close()


@pytest.mark.parametrize("option", ["wrong_label", "wrong_daemon"])
def test_mismatched_environment_cannot_be_deleted(tmp_path, monkeypatch, option):
    root, session, commands = setup(tmp_path, monkeypatch, **{option: True})
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(lease_recover.recover_lease(root, confirm_sessions=[session]))
    assert not any("rm" in command for command in commands)
    receipt = json.loads(next(root.glob("recovery-*.json")).read_text())
    assert receipt["status"] == "failed" and receipt["resources_absent"] is False


def test_repeated_cancellation_retains_owner_lock_until_deletes_finish(tmp_path, monkeypatch):
    root, session, _ = setup(tmp_path, monkeypatch)
    async def exercise():
        started, finish = asyncio.Event(), asyncio.Event()
        original = lease_recover.LocalWorkspaceLease.execute
        async def execute(host, argv, *, timeout):
            if len(argv) > 2 and argv[2] == "rm":
                started.set()
                await finish.wait()
            return await original(host, argv, timeout=timeout)
        monkeypatch.setattr(lease_recover.LocalWorkspaceLease, "execute", execute)
        pending = asyncio.create_task(lease_recover.recover_lease(root, confirm_sessions=[session]))
        try:
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(3):
                pending.cancel()
                await asyncio.sleep(.01)
                assert not pending.done()
            with pytest.raises(RuntimeError, match="active"):
                OwnerLock(root / "owner.lock")
        finally:
            finish.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 5)
        with OwnerLock(root / "owner.lock"):
            pass
        receipt = json.loads(next(root.glob("recovery-*.json")).read_text())
        assert receipt["status"] == "cleaned"
    asyncio.run(exercise())


@pytest.mark.parametrize("flags", [[], ["--cleanup"], ["--discard-uncollected"]])
def test_cli_requires_deletion_and_data_loss_confirmation(tmp_path, monkeypatch, flags):
    cleanup = AsyncMock()
    monkeypatch.setattr(lease_recover, "recover_lease", cleanup)
    with pytest.raises(SystemExit) as error:
        lease_recover.main(["--lease-dir", str(tmp_path), "--confirm-session", "fixture", *flags])
    assert error.value.code == 2
    cleanup.assert_not_called()
