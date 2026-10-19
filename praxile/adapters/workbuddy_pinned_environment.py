"""Opt-in local, content-addressed WorkBuddy images; no task-file rewriting."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
import yaml

from workbuddy_bench.runner.docker_environment import WorkBuddyDockerEnvironment


class PinnedWorkBuddyEnvironment(WorkBuddyDockerEnvironment):
    """Keep upstream lifecycle and verifier behavior, replace only image selection.

    The Dockerfile digest is a caller assertion, not a build attestation.
    Freeze the full task tree separately using native_pair --freeze-local-inputs.
    """

    def __init__(self, *, environment_dir, task_env_config, trial_paths,
                 pinned_image_id, dockerfile_sha256, platform, **kwargs):
        if not isinstance(pinned_image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", pinned_image_id):
            raise ValueError("pinned_image_id must be a full local image ID, not a tag")
        if not isinstance(dockerfile_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", dockerfile_sha256):
            raise ValueError("explicit Dockerfile SHA256 required")
        if platform not in {"linux/amd64", "linux/arm64"}:
            raise ValueError("unsupported pinned image platform")
        if task_env_config.docker_image not in (None, pinned_image_id):
            raise ValueError("cannot override a task-declared image")
        self._pinned_image_id = pinned_image_id
        self._pinned_platform = platform
        self._pinned_source = Path(environment_dir)
        self._dockerfile_sha256 = dockerfile_sha256
        self._image_record = trial_paths.trial_dir / "praxile-pinned-environment.json"
        self._compose_sha256 = None
        self._check_source()
        copied = task_env_config.model_copy(deep=True, update={"docker_image": pinned_image_id})
        super().__init__(environment_dir=environment_dir, task_env_config=copied,
                         trial_paths=trial_paths, **kwargs)

    def _check_source(self):
        source = self._pinned_source / "Dockerfile"
        if source.is_symlink() or not source.is_file():
            raise ValueError("regular Dockerfile required")
        if hashlib.sha256(source.read_bytes()).hexdigest() != self._dockerfile_sha256:
            raise ValueError("Dockerfile digest mismatch")
        # Only the existing broker-host mapping is allowed; no image/services override.
        compose_hashes = {}
        for name in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml"):
            path = self._pinned_source / name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                    raise ValueError("unsupported task compose file")
                raw = path.read_bytes()
                allowed = {"services": {"main": {"extra_hosts": ["host.docker.internal:host-gateway"]}}}
                if yaml.safe_load(raw) != allowed:
                    raise ValueError("task compose overrides are unsupported in pinned mode")
                compose_hashes[name] = hashlib.sha256(raw).hexdigest()
        if len(compose_hashes) > 1:
            raise ValueError("multiple task compose files are unsupported")
        if self._compose_sha256 is not None and compose_hashes != self._compose_sha256:
            raise ValueError("task compose digest mismatch")
        self._compose_sha256 = compose_hashes

    def _compose_env_vars(self, include_os_env=True):
        values = super()._compose_env_vars(include_os_env=include_os_env)
        values["DOCKER_DEFAULT_PLATFORM"] = self._pinned_platform
        return values

    async def start(self, force_build):
        if force_build:
            raise ValueError("pinned mode forbids force_build")
        self._check_source()
        inspected = await asyncio.to_thread(subprocess.run,
            ["docker", "image", "inspect", self._pinned_image_id],
            capture_output=True, text=True, check=True, timeout=15)
        images = json.loads(inspected.stdout)
        if len(images) != 1 or images[0].get("Id") != self._pinned_image_id:
            raise ValueError("local image identity mismatch")
        if f"{images[0].get('Os')}/{images[0].get('Architecture')}" != self._pinned_platform:
            raise ValueError("local image platform mismatch")
        await super().start(force_build=False)
        observed = await self.exec("uname -m", timeout_sec=10)
        expected = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}[self._pinned_platform]
        if observed.return_code != 0 or observed.stdout.strip() != expected:
            raise ValueError("running container platform mismatch")
        self._image_record.write_text(json.dumps({
            "schema_version": "praxile.pinned_environment.v1",
            "image_id": self._pinned_image_id, "platform": self._pinned_platform,
            "dockerfile_sha256": self._dockerfile_sha256,
            "compose_sha256": self._compose_sha256,
            "runtime_architecture": expected, "build_attestation_verified": False,
            "task_files_rewritten": False}, sort_keys=True) + "\n")
