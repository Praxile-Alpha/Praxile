import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from praxile.browser import BrowserEnv
from praxile.config import Config, ProjectPaths, default_config

pytestmark = [pytest.mark.resource, pytest.mark.integration, pytest.mark.browser_resource,
              pytest.mark.timeout(60)]


@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_BROWSER"), reason="explicit Chromium integration opt-in required")
def test_real_browser_session_interaction_and_capture(tmp_path):
    pytest.importorskip("playwright.sync_api")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "https://example.com/")
                self.end_headers()
                return
            body = b'''<input id="name"><button onclick="document.querySelector('#result').textContent=document.querySelector('#name').value;this.hidden=true">Save</button><output id="result"></output>'''
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["browser"].update(enabled=True, allow_interactions=True, timeout_ms=5000)
    config.data["browser"]["observation_probes"] = {
        "save_visible": {"origin": f"http://127.0.0.1:{server.server_port}", "selector": "button", "kind": "visible"},
        "input_count": {"origin": f"http://127.0.0.1:{server.server_port}", "selector": "input", "kind": "count"}}
    try:
        with BrowserEnv(config) as browser:
            assert browser.interact("navigate", url=f"http://127.0.0.1:{server.server_port}").status == "success"
            state = browser.observe_state()
            assert state["session_active"] is True
            assert state["origin"] == f"http://127.0.0.1:{server.server_port}"
            assert state["chromium_version"]
            assert state["dom"] == {"save_visible": True, "input_count": 1}
            profile = state["dom_profile_sha256"]
            assert browser.interact("fill", selector="#name", value="Saved state").status == "success"
            assert browser.interact("click", selector="button").status == "success"
            after = browser.observe_state()
            assert after["dom"] == {"save_visible": False, "input_count": 1}
            assert after["dom_profile_sha256"] == profile
            assert browser.interact("assert_text", selector="#result", value="Saved state").status == "success"
            shot = browser.interact("capture")
            assert shot.status == "success"
            image = tmp_path / shot.data["artifact"]
            assert image.read_bytes().startswith(b"\x89PNG")
            assert not shot.data["visual_quality_verified"]
            assert browser.interact("navigate", url="https://example.com").status == "blocked"
            redirected = browser.interact("navigate", url=f"http://127.0.0.1:{server.server_port}/redirect")
            assert redirected.status in {"blocked", "failure"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.skipif(not os.environ.get("PRAXILE_TEST_BROWSER"), reason="explicit Chromium integration opt-in required")
@pytest.mark.parametrize("operation", ["open", "screenshot"])
def test_one_shot_browser_blocks_redirects_and_external_subresources(tmp_path, operation):
    pytest.importorskip("playwright.sync_api")
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            forbidden = f"http://localhost:{self.server.server_port}/private"
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", forbidden)
                self.end_headers()
                return
            body = f'<title>Fixture</title><img src="{forbidden}"><p>Public page</p>'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["browser"].update(enabled=True, allowed_hosts=["127.0.0.1"], timeout_ms=5000)
    try:
        with BrowserEnv(config) as browser:
            result = getattr(browser, operation)(f"http://127.0.0.1:{server.server_port}/")
            assert result.status == "success"
            if operation == "screenshot":
                assert (tmp_path / result.data["artifact"]).read_bytes().startswith(b"\x89PNG")
            assert browser._session_context is None
            redirected = getattr(browser, operation)(f"http://127.0.0.1:{server.server_port}/redirect")
            assert redirected.status in {"blocked", "failure"}
            assert "/private" not in requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
