import asyncio
import json

import pytest

from praxile.eval import lease_inspect
from praxile.eval.lease import ExecutionResult


def setup(tmp_path, monkeypatch, *, daemon="fixture", present=False, unavailable=False):
    root = tmp_path / ("harbor-lease-" + "a" * 32)
    root.mkdir()
    data = {"schema_version": "praxile.harbor_ownership.v1", "lease_id": root.name,
            "owner_pid": 1, "image": "fixture", "generations": [{
                "session": "praxile-lease-" + "b" * 32, "daemon_id": "fixture", "phase": "ready"}]}
    (root / "ownership.json").write_text(json.dumps(data))
    calls = []
    class Host:
        def __init__(self, root):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, argv, *, timeout):
            calls.append(argv)
            assert argv[0] == "docker"
            if unavailable:
                return ExecutionResult(1, "", "fixture")
            if argv[1] == "info":
                return ExecutionResult(0, daemon, "")
            assert argv[2] == "ls"
            assert "label=com.docker.compose.project=praxile-lease-" + "b" * 32 in argv
            return ExecutionResult(0, "owned-resource" if present else "", "")
    monkeypatch.setattr(lease_inspect, "LocalWorkspaceLease", Host)
    return root, calls


@pytest.mark.parametrize("present", [False, True])
def test_observes_resources_without_changing_record(tmp_path, monkeypatch, present):
    root, calls = setup(tmp_path, monkeypatch, present=present)
    before = (root / "ownership.json").read_bytes()
    result = asyncio.run(lease_inspect.inspect_lease(root))
    assert result["resources_absent"] is (not present)
    assert result["owner_liveness"] == "not_established"
    assert len(calls) == 4
    assert (root / "ownership.json").read_bytes() == before


def test_different_daemon_never_means_clean(tmp_path, monkeypatch):
    root, calls = setup(tmp_path, monkeypatch, daemon="other")
    result = asyncio.run(lease_inspect.inspect_lease(root))
    assert result["resources_absent"] is False
    assert result["generations"][0]["observation"] == "daemon_mismatch"
    assert len(calls) == 1


def test_unavailable_daemon_is_unknown(tmp_path, monkeypatch):
    root, _ = setup(tmp_path, monkeypatch, unavailable=True)
    with pytest.raises(RuntimeError, match="unknown"):
        asyncio.run(lease_inspect.inspect_lease(root))


def test_untrusted_session_cannot_broaden_query(tmp_path, monkeypatch):
    root, calls = setup(tmp_path, monkeypatch)
    path = root / "ownership.json"
    data = json.loads(path.read_text())
    data["generations"][0]["session"] = ""
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        asyncio.run(lease_inspect.inspect_lease(root))
    assert calls == []
