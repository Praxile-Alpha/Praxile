"""Read-only reconciliation of recorded Harbor sessions with the Docker daemon."""
import argparse
import asyncio
import json
from pathlib import Path
import re

from .artifact_read import read_artifact
from .lease import LocalWorkspaceLease


def read_ownership(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or not re.fullmatch(r"harbor-lease-[a-f0-9]{32}", root.name):
        raise ValueError("expected an owned lease directory")
    info = root.stat()
    data = read_artifact(root, "ownership.json", root_identity=(info.st_dev, info.st_ino), max_bytes=1024**2)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate ownership field")
            value[key] = item
        return value
    try:
        record = json.loads(data, object_pairs_hook=unique)
    except RecursionError as error:
        raise ValueError("ownership nesting exceeds limit") from error
    if (not isinstance(record, dict) or record.get("schema_version") != "praxile.harbor_ownership.v1"
            or record.get("lease_id") != root.name or type(record.get("owner_pid")) is not int
            or record["owner_pid"] <= 0):
        raise ValueError("invalid lease ownership identity")
    generations = record.get("generations")
    if not isinstance(generations, list) or not 1 <= len(generations) <= 1000:
        raise ValueError("invalid generation history")
    sessions = set()
    for item in generations:
        if (not isinstance(item, dict) or set(item) != {"session", "daemon_id", "phase"}
                or not isinstance(item["session"], str) or not re.fullmatch(r"praxile-lease-[a-f0-9]{32}", item["session"])
                or item["session"] in sessions or not isinstance(item["daemon_id"], str)
                or not 1 <= len(item["daemon_id"]) <= 256
                or not isinstance(item["phase"], str)
                or item["phase"] not in {"starting", "ready", "cleanup_verified"}):
            raise ValueError("invalid ownership generation")
        sessions.add(item["session"])
    return record


async def inspect_lease(root):
    root = Path(root)
    record = read_ownership(root)
    result = {"schema_version": "praxile.harbor_lease_inspection.v1", "lease_id": record["lease_id"],
              "owner_pid_recorded": record["owner_pid"], "owner_liveness": "not_established",
              "read_only": True, "generations": [], "resources_absent": False}
    async with LocalWorkspaceLease(root) as host:
        async def docker(*args):
            response = await host.execute(["docker", *args], timeout=30)
            if response.timed_out or response.returncode:
                raise RuntimeError("Docker inspection unavailable; resource state is unknown")
            return response.stdout.strip()
        current_daemon = await docker("info", "--format", "{{.ID}}")
        for generation in record["generations"]:
            item = {"session": generation["session"], "recorded_phase": generation["phase"]}
            result["generations"].append(item)
            if not current_daemon or generation["daemon_id"] != current_daemon:
                item["observation"] = "daemon_mismatch"
                continue
            label = "label=com.docker.compose.project=" + generation["session"]
            resources = {}
            for kind in ("container", "network", "volume"):
                output = await docker(kind, "ls", "--filter", label, "-q", *(["-a"] if kind == "container" else []))
                resources[kind] = output.splitlines() if output else []
            item["resources"] = resources
            item["observation"] = "present" if any(resources.values()) else "absent"
        result["resources_absent"] = all(item["observation"] == "absent" for item in result["generations"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lease-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(inspect_lease(args.lease_dir))
    except (ValueError, OSError, RuntimeError):
        parser.exit(2, "Lease inspection failed; resource state is unknown. No cleanup attempted.\n")
    print(json.dumps(result, indent=2))
    return 0 if result["resources_absent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
