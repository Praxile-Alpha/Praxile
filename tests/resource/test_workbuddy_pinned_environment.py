"""Optional WorkBuddy 0.18 environment integration, with explicit Docker opt-in."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("workbuddy_bench")
from harbor.models.task.config import EnvironmentConfig
from harbor.models.trial.paths import TrialPaths
from praxile.adapters.workbuddy_pinned_environment import PinnedWorkBuddyEnvironment

pytestmark = [pytest.mark.resource, pytest.mark.integration]


def arguments(tmp_path):
    source = tmp_path / "environment"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    paths = TrialPaths(tmp_path / "trial")
    paths.mkdir()
    return dict(environment_dir=source, environment_name="praxile-pinned",
        session_id="pinned-" + tmp_path.name.lower().replace("_", "-")[-24:],
        trial_paths=paths, task_env_config=EnvironmentConfig(workdir="/workspace"),
        pinned_image_id="sha256:" + "a" * 64, platform="linux/amd64",
        dockerfile_sha256=hashlib.sha256((source / "Dockerfile").read_bytes()).hexdigest())


@pytest.mark.parametrize("change", ["tag", "digest", "compose", "declared_image", "platform"])
def test_rejects_ambiguous_image_selection(tmp_path, change):
    args = arguments(tmp_path)
    if change == "tag":
        args["pinned_image_id"] = "python:latest"
    elif change == "digest":
        args["dockerfile_sha256"] = "0" * 64
    elif change == "compose":
        (args["environment_dir"] / "docker-compose.yaml").write_text("services: {}")
    elif change == "declared_image":
        args["task_env_config"].docker_image = "python:3.12"
    else:
        args["platform"] = "windows/amd64"
    with pytest.raises(ValueError):
        PinnedWorkBuddyEnvironment(**args)


def test_preserves_config_and_rechecks_before_start(tmp_path):
    args = arguments(tmp_path)
    env = PinnedWorkBuddyEnvironment(**args)
    assert args["task_env_config"].docker_image is None
    assert env.task_env_config.docker_image == args["pinned_image_id"]
    assert env._compose_env_vars()["DOCKER_DEFAULT_PLATFORM"] == "linux/amd64"
    with pytest.raises(ValueError, match="force_build"):
        asyncio.run(env.start(True))
    (args["environment_dir"] / "Dockerfile").write_text("changed")
    with pytest.raises(ValueError, match="digest mismatch"):
        asyncio.run(env.start(False))


def test_host_mapping_is_frozen(tmp_path):
    args = arguments(tmp_path)
    path = args["environment_dir"] / "docker-compose.yaml"
    path.write_text("services:\n  main:\n    extra_hosts:\n      - host.docker.internal:host-gateway\n")
    env = PinnedWorkBuddyEnvironment(**args)
    assert env._compose_sha256["docker-compose.yaml"] == hashlib.sha256(path.read_bytes()).hexdigest()
    path.unlink()
    with pytest.raises(ValueError, match="compose digest"):
        asyncio.run(env.start(False))


@pytest.mark.slow
@pytest.mark.timeout(120)
@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_PINNED_IMAGE"), reason="explicit local image required")
def test_real_pinned_environment_lifecycle(tmp_path):
    args = arguments(tmp_path)
    args["pinned_image_id"] = os.environ["PRAXILE_TEST_PINNED_IMAGE"]
    source = Path(os.environ["PRAXILE_TEST_PINNED_ENVIRONMENT"])
    args["environment_dir"] = source
    args["dockerfile_sha256"] = os.environ["PRAXILE_TEST_DOCKERFILE_SHA256"]
    before = (source / "Dockerfile").read_bytes()
    env = PinnedWorkBuddyEnvironment(**args)

    async def exercise():
        try:
            await env.start(False)
            result = await env.exec("python -c 'import pandas, openpyxl; print(\"available\")'", timeout_sec=20)
            assert result.return_code == 0 and result.stdout.strip() == "available"
            record = json.loads(env._image_record.read_text())
            assert record["image_id"] == args["pinned_image_id"]
            assert record["runtime_architecture"] == "x86_64"
        finally:
            await env.stop(delete=True)
    asyncio.run(exercise())
    assert (source / "Dockerfile").read_bytes() == before
    assert args["task_env_config"].docker_image is None
