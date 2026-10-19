"""Real blocked sockets, not mocked transport calls or live paid providers."""
import json
import os
import socket
import subprocess
import sys
import threading

import pytest


pytestmark = [pytest.mark.resource, pytest.mark.integration,
              pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")]


@pytest.mark.parametrize("transport", ["SimpleHTTPTransport", "HttpxTransport"])
@pytest.mark.parametrize("cancel", [False, True])
def test_supervision_closes_blocked_model_socket(tmp_path, transport, cancel):
    if transport == "HttpxTransport":
        pytest.importorskip("httpx")
    status = tmp_path / "supervision.json"
    marker = tmp_path / "cancel"
    disconnected = threading.Event()
    received = threading.Event()
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(8)
        port = listener.getsockname()[1]

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(8)
                    request = b""
                    while b"\r\n\r\n" not in request:
                        part = connection.recv(4096)
                        if not part:
                            raise AssertionError("request closed before headers")
                        request += part
                    received.set()
                    if cancel:
                        marker.touch()
                    # Never send a response. The worker's own read timeout is 60s.
                    while connection.recv(4096):
                        pass
                    disconnected.set()
            except BaseException as error:
                errors.append(error)

        server = threading.Thread(target=serve)
        server.start()
        program = (
            f"from praxile.http_transport import {transport}; "
            f"transport = {transport}(timeout_seconds=60, max_retries=0); "
            f"transport.post_json('http://127.0.0.1:{port}/v1/chat/completions', "
            "payload={'messages': []}, headers={})"
        )
        argv = [sys.executable, "-m", "praxile.eval.process_supervisor",
                "--timeout", "2", "--status", str(status),
                "--cancel-file", str(marker), "--", sys.executable, "-c", program]
        process = None
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
            _, stderr = process.communicate(timeout=10)
        finally:
            try:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=5)
            finally:
                server.join(timeout=9)
        assert not server.is_alive()
        assert not errors, errors
        assert received.is_set()
        assert disconnected.is_set(), "owned process exited but HTTP socket remained open"
        assert process.returncode == (143 if cancel else 124), stderr.decode()
        receipt = json.loads(status.read_text())
        assert receipt["status"] == ("cancelled" if cancel else "timed_out")
        assert receipt["worker_reaped"] is True
        assert 0 < receipt["elapsed_seconds"] < 8
        assert receipt["remote_request_cancelled"] is None
        assert receipt["remote_usage_known"] is False
