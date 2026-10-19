import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from praxile.eval.native_worker import benchmark_config


pytestmark = [pytest.mark.resource, pytest.mark.integration]


def test_worker_runs_real_native_loop_against_fixture_endpoint(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            response = json.dumps({"choices": [{"message": {"content": json.dumps({
                "type": "finish", "summary": "fixture finished", "status": "completed",
            })}}], "usage": {"prompt_tokens": 12, "completion_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instruction = tmp_path / "instruction.txt"
    instruction.write_text("Read the available project and summarize it.")
    result = tmp_path / "result.json"
    try:
        process = subprocess.run([
            sys.executable, "-m", "praxile.eval.native_worker", "--workspace", str(workspace),
            "--instruction", str(instruction), "--result", str(result), "--model", "fixture",
            "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--max-steps", "2",
        ], capture_output=True, text=True, timeout=20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert process.returncode == 0, process.stderr
    report = json.loads(result.read_text())
    assert requests
    assert report["official_evaluation"] == "not_run"
    assert report["trajectory"]["cost"]["prompt_tokens"] == 12
    assert report["trajectory"]["result"]["status"] == "completed"
    assert list((workspace / ".praxile/experience/trajectories").glob("*.json"))


def test_existing_state_cannot_enter_frozen_execution(tmp_path):
    (tmp_path / ".praxile").mkdir()
    with pytest.raises(ValueError, match="without .praxile"):
        benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=2)


@pytest.mark.parametrize("endpoint", ["https://secret@provider/v1", "https://provider/v1?key=secret", "file:///tmp/model"])
def test_worker_rejects_secret_bearing_or_non_http_endpoints(tmp_path, endpoint):
    with pytest.raises(ValueError, match="base_url"):
        benchmark_config(tmp_path, model="fixture", base_url=endpoint, max_steps=2)
