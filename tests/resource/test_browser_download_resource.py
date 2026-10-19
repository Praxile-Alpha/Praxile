import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from praxile.config import Config, ProjectPaths, default_config
from praxile.tools import ToolRegistry

pytestmark = [pytest.mark.resource, pytest.mark.integration]


@pytest.fixture
def download_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/ok")
                self.end_headers()
                return
            self.send_response(200)
            if self.path not in {"/stream", "/trickle"}:
                self.send_header("Content-Length", "20" if self.path == "/large" else "7" if self.path == "/short" else "2")
            self.end_headers()
            if self.path == "/trickle":
                try:
                    for _ in range(30):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(.02)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            self.wfile.write(b"x" * 20 if self.path == "/stream" else b"ok")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("path,success", [("/ok", True), ("/large", False), ("/stream", False),
                                         ("/short", False), ("/redirect", False), ("/trickle", False)])
def test_bounded_download_checks_redirects_and_never_publishes_partial_files(tmp_path, download_server, path, success):
    base, requests = download_server
    data = default_config(tmp_path)
    data["browser"].update(enabled=True, allow_downloads=True, allowed_hosts=["127.0.0.1"], max_download_bytes=8)
    if path == "/trickle":
        data["browser"]["timeout_ms"] = 100
    tools = ToolRegistry(Config(data, ProjectPaths(tmp_path)))
    try:
        result = tools.execute({"type": "browser_download", "url": base + path}, task_id="t", step=1)
        assert (result["status"] == "success") is success
        files = list(tmp_path.rglob("download-*"))
        if success:
            artifact = tmp_path / result["data"]["artifact"]
            assert artifact.read_bytes() == b"ok"
            assert result["data"]["sha256"] == hashlib.sha256(b"ok").hexdigest()
            assert result["data"]["authenticated_session"] is False
            assert files == [artifact]
        else:
            assert files == []
        if path == "/redirect":
            assert requests == ["/redirect"]
    finally:
        tools.close()
