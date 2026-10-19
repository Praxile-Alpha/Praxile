"""Bounded public HTTP downloads; no browser cookies or ambient proxy access."""
from __future__ import annotations

import hashlib
import time
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4

from .environment import Observation
from .utils import utc_now


def download(env, url: str) -> Observation:
    if env.config.get("browser", "allow_downloads", default=False) is not True:
        return Observation("blocked", "browser downloads are disabled", risk_level="medium")
    hosts = env.config.get("browser", "allowed_hosts", default=[])
    def check(target):
        parsed = urlparse(target)
        if (not isinstance(hosts, list) or not hosts or env._preflight(target)
                or parsed.username or parsed.password):
            raise ValueError("download URL is outside configured scope")
    try:
        check(url)
        limit = env.config.get("browser", "max_download_bytes", default=10_000_000)
        if type(limit) is not int or not 1 <= limit <= 100_000_000:
            raise ValueError("invalid download limit")
        directory = env._artifact_dir()
        if directory is None or not directory.is_relative_to(env.root.resolve()):
            raise ValueError("download artifact directory escapes project")
        directory.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError):
        return Observation("blocked", "Invalid download URL, size limit or artifact directory", risk_level="medium")

    class ScopedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            check(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    target = directory / ("download-" + uuid4().hex + ".bin")
    staging = target.with_suffix(".partial")
    digest = hashlib.sha256()
    total = 0
    timeout = max(.1, min(env._timeout_ms() / 1000, 120))
    deadline = time.monotonic() + timeout
    try:
        # Explicitly disable inherited proxy credentials and routing.
        opener = build_opener(ProxyHandler({}), ScopedRedirect())
        with opener.open(url, timeout=timeout) as response:
            check(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise ValueError("download size limit")
            with staging.open("xb") as handle:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("download deadline")
                    # read1 returns currently available data; read(n) can keep
                    # waiting for n bytes on a trickle response indefinitely.
                    chunk = response.read1(min(65536, limit - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("download size limit")
                    handle.write(chunk)
                    digest.update(chunk)
            if length is not None and total != int(length):
                raise ValueError("incomplete download")
        staging.replace(target)
        parsed = urlparse(url)
        return Observation("success", "Downloaded public HTTP artifact", {
            "artifact": target.relative_to(env.root).as_posix(), "artifact_type": "download",
            "size_bytes": total, "sha256": digest.hexdigest(), "created_at": utc_now(),
            "source_host": parsed.hostname, "authenticated_session": False,
            "content_safety_verified": False, "semantic_quality_verified": False})
    except Exception as error:
        return Observation("failure", "Download failed", {"error_type": type(error).__name__}, risk_level="medium")
    finally:
        staging.unlink(missing_ok=True)
