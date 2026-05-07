from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .config import Config
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
        page = None
        try:
            browser = self._browser()
            page = browser.new_page()
            page.goto(url, wait_until=self._wait_until(), timeout=self._timeout_ms())
            title = page.title()
        except Exception as exc:
            return Observation("failure", f"browser_open failed: {exc}", {"url": url}, risk_level="low")
        finally:
            self._close_page(page)
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
        page = None
        try:
            browser = self._browser()
            page = browser.new_page(viewport=self._viewport())
            page.goto(url, wait_until=self._wait_until(), timeout=self._timeout_ms())
            page.screenshot(path=str(target), full_page=True)
            title = page.title()
        except Exception as exc:
            return Observation("failure", f"browser_screenshot failed: {exc}", {"url": url}, risk_level="low")
        finally:
            self._close_page(page)
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
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Observation("blocked", f"browser URL must be absolute http(s): {url}", risk_level="medium")
        allowed_hosts = self.config.get(
            "browser",
            "allowed_hosts",
            default=["localhost", "127.0.0.1", "::1"],
        )
        if allowed_hosts and parsed.hostname not in allowed_hosts:
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
        if self._browser_instance is not None:
            try:
                self._browser_instance.close()
            finally:
                self._browser_instance = None
        if self._playwright_context is not None:
            try:
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
        if not path_is_relative_to(target, allowed_root):
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
