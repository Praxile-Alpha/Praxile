"""Real owner SIGKILL and fresh-process inspection; no production recovery claim."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

import pytest

pytest.importorskip("harbor")
from praxile.eval.lease import LocalWorkspaceLease
from praxile.eval.lease_inspect import inspect_lease, read_ownership

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.slow,
              pytest.mark.skipif(os.name != "posix" or not os.environ.get("PRAXILE_TEST_LEASE_IMAGE"),
                                 reason="requires POSIX and explicit pinned Docker image")]


def test_sigkill_owner_preserves_discoverable_resources(tmp_path):
    async def exercise():
        code = """
import asyncio, sys
from pathlib import Path
from praxile.eval.harbor_lease import HarborDockerLease
async def main():
    lease = HarborDockerLease(Path(sys.argv[1]), image=sys.argv[2])
    await lease.prepare()
    print('LEASE_READY=' + str(lease.root), flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, str(tmp_path), os.environ["PRAXILE_TEST_LEASE_IMAGE"],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        root = None
        try:
            async with asyncio.timeout(90):
                while True:
                    line = await child.stdout.readline()
                    if not line:
                        raise AssertionError("lease owner exited before readiness")
                    if line.startswith(b"LEASE_READY="):
                        root = Path(line.decode().strip().split("=", 1)[1])
                        break
            assert root.parent.resolve() == tmp_path.resolve()
            ownership = read_ownership(root)
            assert ownership["owner_pid"] == child.pid
            assert ownership["generations"][0]["phase"] == "ready"
            before = (root / "ownership.json").read_bytes()
            os.killpg(child.pid, signal.SIGKILL)
            assert await asyncio.wait_for(child.wait(), 10) == -signal.SIGKILL
            await child.communicate()

            # Start a fresh process, so observations cannot come from owner memory.
            async with LocalWorkspaceLease(tmp_path) as host:
                observed = await host.execute([sys.executable, "-m", "praxile.eval.lease_inspect",
                                              "--lease-dir", str(root)], timeout=30)
            assert observed.returncode == 1 and not observed.timed_out
            report = json.loads(observed.stdout)
            assert report["resources_absent"] is False
            assert report["generations"][0]["resources"]["container"]
            assert report["owner_liveness"] == "not_established"
            assert (root / "ownership.json").read_bytes() == before
        finally:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGKILL)
                await asyncio.wait_for(child.wait(), 10)
                await child.communicate()
            # Only this test's generated directories and matching project labels.
            # Never prune or infer ownership from a broad name prefix.
            for owned in tmp_path.glob("harbor-lease-*"):
                if not (owned / "ownership.json").exists():
                    continue
                record = read_ownership(owned)
                assert record["owner_pid"] == child.pid
                observed = await inspect_lease(owned)
                async with LocalWorkspaceLease(tmp_path) as host:
                    for generation in observed["generations"]:
                        assert generation["observation"] in {"present", "absent"}
                        for kind in ("container", "network", "volume"):
                            for resource in generation["resources"][kind]:
                                field = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
                                labels = await host.execute(["docker", kind, "inspect", "--format", field, resource], timeout=30)
                                assert labels.returncode == 0 and not labels.timed_out
                                assert json.loads(labels.stdout)["com.docker.compose.project"] == generation["session"]
                                command = ["docker", kind, "rm"] + (["-f"] if kind == "container" else []) + [resource]
                                removed = await host.execute(command, timeout=30)
                                assert removed.returncode == 0 and not removed.timed_out
                assert (await inspect_lease(owned))["resources_absent"] is True
        assert root is not None
    asyncio.run(exercise())
