"""Read-only benchmark prerequisites; never claim task or model validation."""
from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def inspect_benchmark(benchmark: str, *, online: bool = False, workspace: Path | None = None) -> dict:
    if benchmark not in {"workbuddy", "jobbench"}:
        raise ValueError("unsupported benchmark health profile")
    expected = "0.18.0" if benchmark == "workbuddy" else "0.22.0"
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    try:
        actual = importlib.metadata.version("harbor")
    except importlib.metadata.PackageNotFoundError:
        actual = None
    add("harbor_version", "ok" if actual == expected else "unavailable",
        {"expected": expected, "installed": actual})
    if actual == expected:
        # Import in a bounded child: plugins cannot leave state in the CLI process.
        imports = "from praxile.adapters.harbor_native import PraxileAgent"
        if benchmark == "workbuddy":
            imports += "; from workbuddy_bench.judge import CompositeVerifier; from workbuddy_bench.runner.docker_environment import WorkBuddyDockerEnvironment"
        try:
            result = subprocess.run([sys.executable, "-c", imports], capture_output=True, timeout=15)
            add("runtime_imports", "ok" if result.returncode == 0 else "unavailable",
                "imports succeeded" if result.returncode == 0 else "plugin import failed; inspect isolated installation")
        except (OSError, subprocess.TimeoutExpired):
            add("runtime_imports", "unavailable", "plugin import failed or timed out")
    else:
        add("runtime_imports", "not_checked", "install the pinned Harbor environment first")
    docker = shutil.which("docker")
    daemon_platform = None
    add("docker_cli", "ok" if docker else "unavailable", "found" if docker else "Docker CLI missing")
    if online and docker:
        try:
            result = subprocess.run([docker, "version", "--format", "{{json .Server}}"],
                                    capture_output=True, timeout=10)
            server = json.loads(result.stdout) if result.returncode == 0 else {}
            reachable = isinstance(server, dict) and isinstance(server.get("Version"), str) and bool(server["Version"])
            if reachable:
                os_name, arch = server.get("Os"), server.get("Arch")
                if os_name in {"linux", "windows"} and arch in {"amd64", "arm64", "arm", "386", "ppc64le", "s390x"}:
                    daemon_platform = f"{os_name}/{arch}"
            add("docker_daemon", "ok" if reachable else "unavailable",
                "reachable" if reachable else "daemon query failed")
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
            add("docker_daemon", "unavailable", "daemon query failed or timed out")
    else:
        add("docker_daemon", "not_checked", "use --online with Docker installed")
    present = bool(os.environ.get("PRAXILE_BENCHMARK_API_KEY"))
    add("executor_credential", "configured" if present else "not_configured",
        "PRAXILE_BENCHMARK_API_KEY presence only; container forwarding is separate")
    add("model_endpoint", "not_checked", "no model request made; subscription compatibility and model ID are unverified")
    add("official_judge", "not_checked", "requires frozen dataset and per-task scoring configuration")
    add("architecture", "observed" if daemon_platform else "not_checked", {
        "host_os": platform.system(), "host": platform.machine(), "docker_server_platform": daemon_platform,
        "image_platform_verified": False,
        "note": "daemon architecture is not task image support; amd64-only images on arm64 need separate validation"})
    try:
        directory = (workspace or Path.cwd()).resolve()
        while not directory.exists() and directory != directory.parent:
            directory = directory.parent
        usage = shutil.disk_usage(directory)
        add("disk_access", "ok", {"filesystem_path": str(directory), "total_bytes": usage.total,
            "free_bytes": usage.free, "required_bytes": None,
            "note": "host filesystem only; Docker VM image storage and task requirements are not verified"})
    except OSError:
        add("disk_access", "unavailable", "cannot inspect workspace filesystem capacity")
    add("security_subset", "unsupported" if benchmark == "workbuddy" else "not_applicable",
        "WorkBuddy Security requires separate network isolation validation")
    return {"schema_version": "praxile.benchmark_health.v1", "benchmark": benchmark,
            "infrastructure_available": all(next(item["status"] for item in checks if item["name"] == name) == "ok"
                for name in ("harbor_version", "runtime_imports", "docker_cli", "docker_daemon", "disk_access")),
            "execution_verified": False, "checks": checks}
