"""Explicit, lock-guarded cleanup of recorded Docker generations; never replay."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

from .async_ownership import await_owned
from .lease import LocalWorkspaceLease
from .lease_inspect import inspect_lease, read_ownership
from .owner_lock import OwnerLock
from ..utils import write_json


async def recover_lease(root, *, confirm_sessions):
    root = Path(root)
    record = read_ownership(root)
    identity = record.get("owner_lock")
    if (not isinstance(identity, dict) or set(identity) != {"device", "inode"}
            or any(type(value) is not int or value < 0 for value in identity.values())):
        raise ValueError("lease lacks verifiable owner lock; legacy recovery forbidden")
    sessions = [item["session"] for item in record["generations"]]
    if not isinstance(confirm_sessions, (list, tuple)) or list(confirm_sessions) != sessions:
        raise ValueError("confirm all recorded sessions in order before cleanup")
    with OwnerLock(root / "owner.lock") as owner:
        if owner.identity != identity or read_ownership(root) != record:
            raise ValueError("lease ownership or lock identity changed")
        receipt = {"schema_version": "praxile.lease_recovery.v1", "lease_id": record["lease_id"],
                   "sessions": sessions, "status": "cleaning", "removed": [],
                   "resources_absent": False, "execution_replayed": False}
        path = root / ("recovery-" + uuid.uuid4().hex + ".json")
        write_json(path, receipt)
        async def clean():
            try:
                observed = await inspect_lease(root)
                if any(item["observation"] == "daemon_mismatch" for item in observed["generations"]):
                    raise RuntimeError("recorded Docker daemon is not current")
                async with LocalWorkspaceLease(root) as host:
                    async def docker(*args):
                        result = await host.execute(["docker", *args], timeout=30)
                        if result.timed_out or result.returncode:
                            raise RuntimeError("Docker recovery operation failed")
                        return result.stdout.strip()
                    for generation, snapshot in zip(record["generations"], observed["generations"]):
                        for kind in ("container", "network", "volume"):
                            for resource in snapshot["resources"][kind]:
                                if await docker("info", "--format", "{{.ID}}") != generation["daemon_id"]:
                                    raise RuntimeError("Docker daemon changed during recovery")
                                field = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
                                labels = json.loads(await docker(kind, "inspect", "--format", field, resource))
                                if not isinstance(labels, dict) or labels.get("com.docker.compose.project") != generation["session"]:
                                    raise ValueError("resource ownership label mismatch")
                                await docker(kind, "rm", *(["-f"] if kind == "container" else []), resource)
                                receipt["removed"].append({"kind": kind, "id": resource, "session": generation["session"]})
                                write_json(path, receipt)
                if not (await inspect_lease(root))["resources_absent"]:
                    raise RuntimeError("recovery left resources or daemon identity is unknown")
                receipt.update(status="cleaned", resources_absent=True)
            except BaseException as error:
                receipt.update(status="failed", error_type=type(error).__name__)
                raise
            finally:
                write_json(path, receipt)
        # Do not release owner exclusion while an issued delete is still running.
        await await_owned(clean())
        return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease-dir", type=Path, required=True)
    parser.add_argument("--confirm-session", nargs="+", required=True)
    parser.add_argument("--cleanup", action="store_true", help="Explicitly authorize deletion of these owned resources")
    parser.add_argument("--discard-uncollected", action="store_true", help="Acknowledge uncollected container/volume data will be lost")
    args = parser.parse_args(argv)
    if not args.cleanup or not args.discard_uncollected:
        parser.error("--cleanup and --discard-uncollected are required; inspect and recover needed artifacts first")
    try:
        path = asyncio.run(recover_lease(args.lease_dir, confirm_sessions=args.confirm_session))
    except KeyboardInterrupt:
        parser.exit(130, "Recovery interrupted; inspect receipts and resources before retrying.\n")
    except (ValueError, OSError, RuntimeError):
        parser.exit(2, "Recovery refused or failed; inspect retained recovery receipts. No execution replay.\n")
    print("Recovery receipt: " + str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
