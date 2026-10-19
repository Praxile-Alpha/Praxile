from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import uuid4

from .config import Config
from .browser_probes import observe_probes, validate_probes
from .environment import Observation
from .utils import path_is_relative_to, slugify, utc_now


class BrowserEnv:
    """Optional Playwright-backed browser adapter for UI evidence."""

    def __init__(self, config: Config, *, sync_playwright_factory: Callable[[], Any] | None = None):
        self.config = config
        self.root = config.paths.root
        self.sync_playwright_factory = sync_playwright_factory
        self._playwright_context: Any | None = None
        self._playwright: Any | None = None
        self._browser_instance: Any | None = None
        self._closed = False
        self._session_context: Any | None = None
        self._session_page: Any | None = None
        self._observation_probes = validate_probes(config.get("browser", "observation_probes", default={}))

    def observe_state(self) -> dict[str, Any]:
        """Inspect existing handles without launching or navigating a browser."""
        result: dict[str, Any] = {"session_active": False}
        browser = self._browser_instance
        if browser is None:
            return result
        try:
            if not browser.is_connected():
                return result
            version = browser.version
            if isinstance(version, str) and 0 < len(version) <= 100:
                result["chromium_version"] = version
            page = self._session_page
            if page is None or page.is_closed():
                return result
            result["session_active"] = True
            parsed = urlparse(page.url)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                host = parsed.hostname
                if ":" in host:
                    host = f"[{host}]"
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                result["origin"] = f"{parsed.scheme}://{host}:{port}"
            if not self._preflight(page.url):
                result.update(observe_probes(page, self._observation_probes))
        except Exception:
            # Unknown state must not be reported as a matching observation.
            return {}
        return result

    def interact(self, operation: str, *, url: str = "", selector: str = "", value: str = "") -> Observation:
        if self.config.get("browser", "allow_interactions", default=False) is not True:
            return Observation("blocked", "browser interactions require browser.allow_interactions=true", risk_level="medium")
        hosts = self.config.get("browser", "allowed_hosts", default=[])
        if not isinstance(hosts, list) or not hosts or any(not isinstance(host, str) or not host for host in hosts):
            return Observation("blocked", "interactive browser requires explicit allowed_hosts", risk_level="medium")
        if operation not in {"navigate", "click", "fill", "assert_text", "capture"}:
            return Observation("blocked", "unsupported browser interaction", risk_level="medium")
        current = url if operation == "navigate" else getattr(self._session_page, "url", "")
        preflight = self._preflight(current)
        if preflight:
            return preflight
        if urlparse(current).username or urlparse(current).password:
            return Observation("blocked", "embedded URL credentials are forbidden", risk_level="medium")
        try:
            if self._session_context is None:
                context = self._restricted_context()
                self._session_context = context
                self._session_page = context.new_page()
            page = self._session_page
            if operation == "navigate":
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms())
            elif operation == "click":
                page.locator(selector).click(timeout=self._timeout_ms())
            elif operation == "fill":
                page.locator(selector).fill(value, timeout=self._timeout_ms())
            elif operation == "capture":
                directory = self._artifact_dir()
                if directory is None:
                    return Observation("blocked", "invalid screenshot artifact directory", risk_level="medium")
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / ("session-" + uuid4().hex + ".png")
                page.screenshot(path=str(target), full_page=True, timeout=self._timeout_ms())
                return Observation("success", "Captured current browser state", {
                    "operation": operation, "url": page.url,
                    "artifact": target.relative_to(self.root).as_posix(), "artifact_type": "screenshot",
                    "created_at": utc_now(), "visual_quality_verified": False})
            else:
                actual = page.locator(selector).inner_text(timeout=self._timeout_ms())
                matched = actual == value
                return Observation("success" if matched else "failure", "Text assertion matched" if matched else "Text assertion did not match",
                                   {"operation": operation, "matched": matched, "url": page.url})
            if self._preflight(page.url):
                self._close_session()
                return Observation("blocked", "navigation left the allowed HTTP origin set", risk_level="medium")
            return Observation("success", "Browser interaction completed", {"operation": operation, "url": page.url})
        except Exception as exc:
            self._close_session()
            # Playwright errors may contain typed values or authenticated URLs.
            return Observation("failure", "Browser interaction failed", {"operation": operation,
                               "error_type": type(exc).__name__}, risk_level="medium")

    def _route_session_request(self, route: Any) -> None:
        url = route.request.url
        if self._preflight(url):
            route.abort()
        else:
            response = None
            try:
                # continue_ follows redirect chains without routing each hop.
                # Fetch only this response and fail closed on all redirects.
                response = route.fetch(max_redirects=0, timeout=self._timeout_ms())
                if response.status in {301, 302, 303, 307, 308}:
                    route.abort()
                else:
                    route.fulfill(response=response)
            except Exception:
                route.abort()
            finally:
                if response is not None:
                    response.dispose()

    def _restricted_context(self) -> Any:
        context = self._browser().new_context(viewport=self._viewport(), service_workers="block", accept_downloads=False)
        try:
            context.route("**/*", self._route_session_request)
            # Socket traffic bypasses HTTP routing; old Playwright fails closed.
            context.route_web_socket("**/*", lambda socket: socket.close())
            return context
        except BaseException:
            self._close_context(context)
            raise

    @staticmethod
    def _close_context(context: Any | None) -> None:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    def _close_session(self) -> None:
        context, self._session_context = self._session_context, None
        self._session_page = None
        self._close_context(context)

    def __enter__(self) -> "BrowserEnv":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - destructor timing is runtime-dependent
        try:
            self.close()
        except Exception:
            pass

    def open(self, url: str) -> Observation:
        preflight = self._preflight(url)
        if preflight:
            return preflight
        context = None
        try:
            context = self._restricted_context()
            page = context.new_page()
            page.goto(url, wait_until=self._wait_until(), timeout=self._timeout_ms())
            if self._preflight(page.url):
                return Observation("blocked", "navigation left the allowed HTTP host set", risk_level="medium")
            title = page.title()
        except Exception as exc:
            return Observation("failure", "browser_open failed", {"error_type": type(exc).__name__}, risk_level="low")
        finally:
            self._close_context(context)
        return Observation("success", f"Opened {url}", {"url": url, "title": title})

    def screenshot(self, url: str, *, name: str | None = None) -> Observation:
        preflight = self._preflight(url)
        if preflight:
            return preflight
        artifact_dir = self._artifact_dir()
        if artifact_dir is None:
            return Observation("blocked", "browser artifact_dir must stay inside .praxile/experience/artifacts", risk_level="medium")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{slugify(name or url, max_length=60)}.png"
        target = artifact_dir / filename
        if target.is_symlink():
            return Observation("blocked", "screenshot target must not be a symlink", risk_level="medium")
        context = None
        try:
            context = self._restricted_context()
            page = context.new_page()
            page.goto(url, wait_until=self._wait_until(), timeout=self._timeout_ms())
            if self._preflight(page.url):
                return Observation("blocked", "navigation left the allowed HTTP host set", risk_level="medium")
            page.screenshot(path=str(target), full_page=True, timeout=self._timeout_ms())
            title = page.title()
        except Exception as exc:
            return Observation("failure", "browser_screenshot failed", {"error_type": type(exc).__name__}, risk_level="low")
        finally:
            self._close_context(context)
        rel = target.relative_to(self.root).as_posix()
        return Observation(
            "success",
            f"Captured screenshot artifact: {rel}",
            {
                "url": url,
                "title": title,
                "artifact": rel,
                "artifact_type": "screenshot",
                "created_at": utc_now(),
            },
        )

    def _preflight(self, url: str) -> Observation | None:
        if not self.config.get("browser", "enabled", default=False):
            return Observation("blocked", "browser adapter is disabled; set browser.enabled=true", risk_level="low")
        try:
            parsed = urlparse(url)
        except ValueError:
            return Observation("blocked", "invalid browser URL", risk_level="medium")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Observation("blocked", "browser URL must be absolute http(s)", risk_level="medium")
        if parsed.username or parsed.password:
            return Observation("blocked", "embedded URL credentials are forbidden", risk_level="medium")
        allowed_hosts = self.config.get(
            "browser",
            "allowed_hosts",
            default=["localhost", "127.0.0.1", "::1"],
        )
        if not isinstance(allowed_hosts, list) or not allowed_hosts or parsed.hostname not in allowed_hosts:
            return Observation("blocked", f"browser host not allowed: {parsed.hostname}", risk_level="medium")
        return None

    def _sync_playwright(self) -> Any:
        if self.sync_playwright_factory:
            return self.sync_playwright_factory()
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError("Playwright is not installed; install praxile[browser] and run `python -m playwright install chromium`.") from exc
        return sync_playwright()

    def _browser(self) -> Any:
        if self._browser_instance is not None:
            return self._browser_instance
        self._closed = False
        self._playwright_context = self._sync_playwright()
        self._playwright = self._playwright_context.__enter__()
        self._browser_instance = self._playwright.chromium.launch(headless=True)
        return self._browser_instance

    def close(self) -> None:
        if self._closed:
            return
        self._close_session()
        try:
            if self._browser_instance is not None:
                self._browser_instance.close()
        finally:
            self._browser_instance = None
            try:
                if self._playwright_context is not None:
                    self._playwright_context.__exit__(None, None, None)
            finally:
                self._playwright_context = None
                self._playwright = None
                self._closed = True

    def _close_page(self, page: Any | None) -> None:
        if page is not None and hasattr(page, "close"):
            try:
                page.close()
            except Exception:
                pass

    def _artifact_dir(self) -> Path | None:
        configured = self.config.get("browser", "artifact_dir", default=".praxile/experience/artifacts")
        raw = Path(str(configured))
        target = raw if raw.is_absolute() else self.root / raw
        target = target.resolve(strict=False)
        allowed_root = (self.config.paths.state / "experience" / "artifacts").resolve(strict=False)
        if not path_is_relative_to(allowed_root, self.root.resolve()) or not path_is_relative_to(target, allowed_root):
            return None
        return target / "browser"

    def _timeout_ms(self) -> int:
        return int(self.config.get("browser", "timeout_ms", default=15000))

    def _wait_until(self) -> str:
        configured = os.environ.get("PRAXILE_BROWSER_WAIT_UNTIL")
        if configured:
            return configured
        if os.environ.get("CI") or os.environ.get("PYTEST_CURRENT_TEST"):
            return "domcontentloaded"
        return "networkidle"

    def _viewport(self) -> dict[str, int]:
        width = int(self.config.get("browser", "viewport_width", default=1280))
        height = int(self.config.get("browser", "viewport_height", default=900))
        return {"width": width, "height": height}
