"""Read-only benchmark prerequisites; never claim task or model validation."""
from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys


def inspect_benchmark(benchmark: str, *, online: bool = False) -> dict:
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
    add("docker_cli", "ok" if docker else "unavailable", "found" if docker else "Docker CLI missing")
    if online and docker:
        try:
            result = subprocess.run([docker, "version", "--format", "{{.Server.Version}}"],
                                    capture_output=True, timeout=10)
            reachable = result.returncode == 0 and bool(result.stdout.strip())
            add("docker_daemon", "ok" if reachable else "unavailable",
                "reachable" if reachable else "daemon query failed")
        except (OSError, subprocess.TimeoutExpired):
            add("docker_daemon", "unavailable", "daemon query failed or timed out")
    else:
        add("docker_daemon", "not_checked", "use --online with Docker installed")
    present = bool(os.environ.get("PRAXILE_BENCHMARK_API_KEY"))
    add("executor_credential", "configured" if present else "not_configured",
        "PRAXILE_BENCHMARK_API_KEY presence only; container forwarding is separate")
    add("model_endpoint", "not_checked", "no model request made; subscription compatibility and model ID are unverified")
    add("official_judge", "not_checked", "requires frozen dataset and per-task scoring configuration")
    add("architecture", "not_checked", {"host": platform.machine(),
        "note": "task images may require amd64; host architecture does not prove task support"})
    add("security_subset", "unsupported" if benchmark == "workbuddy" else "not_applicable",
        "WorkBuddy Security requires separate network isolation validation")
    return {"schema_version": "praxile.benchmark_health.v1", "benchmark": benchmark,
            "infrastructure_available": not any(item["status"] == "unavailable" for item in checks),
            "execution_verified": False, "checks": checks}
