import json
import subprocess
import sys
import threading
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from praxile.eval.native_worker import benchmark_config


pytestmark = [pytest.mark.resource, pytest.mark.integration]


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("candidate_mode", ["none", "active", "abstain"])
def test_worker_runs_real_native_loop_against_fixture_endpoint(tmp_path, structured, bound, candidate_mode):
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
    (workspace / "output").mkdir()
    (workspace / "output/fake.docx").write_text("not a ZIP")
    instruction = tmp_path / "instruction.txt"
    instruction.write_text("Read the available project and summarize it.")
    extra = []
    if structured:
        instruction.write_text("If asked to modify a database, save the final copy in output.")
        body = workspace / "task.txt"
        body.write_text("Summarize the available files.")
        extra = ["--task-body-file", "task.txt", "--task-body-sha256", hashlib.sha256(body.read_bytes()).hexdigest()]
    if bound:
        public_contract = tmp_path / "public-contract.json"
        public_contract.write_text(json.dumps({"schema_version": "praxile.deliverable_contract.v1",
            "source": "user_request", "outputs": [{"path": "output/fake.docx",
                "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "structure": {"max_pages": 1}}]}))
        extra += ["--deliverable-contract-file", str(public_contract), "--deliverable-contract-sha256",
                  hashlib.sha256(public_contract.read_bytes()).hexdigest()]
    if candidate_mode != "none":
        from praxile.eval.v2.candidate import ContextCandidate
        candidate = ContextCandidate(candidate_id="development", version="1", title="Fixture",
            candidate_type="context_policy", context_item={"content": "EXPERIMENT_SENTINEL: inspect files before summarizing."},
            confidence=0, evidence_refs=("event:fixture",), expected_effect={"hypothesis": "test transport"},
            applies_to={"repositories": ["fixture/repo"], "task_signals": ["available"],
                        "does_not_apply_when": ["unrelated topic"]})
        candidate_path = tmp_path / "candidate.json"
        candidate_path.write_text(json.dumps(candidate.to_dict()))
        extra += ["--development-candidate", str(candidate_path), "--development-candidate-sha256",
                  hashlib.sha256(candidate_path.read_bytes()).hexdigest(), "--development-only",
                  "--repository", "fixture/repo" if candidate_mode == "active" else "other/repo"]
    result = tmp_path / "result.json"
    try:
        process = subprocess.run([
            sys.executable, "-m", "praxile.eval.native_worker", "--workspace", str(workspace),
            "--instruction", str(instruction), "--result", str(result), "--model", "fixture",
            "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--max-steps", "2",
            "--output-directory", "output",
        ] + extra, capture_output=True, text=True, timeout=20)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert process.returncode == 0, process.stderr
    report = json.loads(result.read_text())
    assert requests
    injected = any("EXPERIMENT_SENTINEL" in message["content"] for request in requests for message in request["messages"])
    assert injected is (candidate_mode == "active")
    if candidate_mode != "none":
        assert report["development_context"]["injected"] is injected
        assert report["development_context"]["activation"]["activated"] is injected
        assert report["development_context"]["promotion_eligible"] is False
    assert report["official_evaluation"] == "not_run"
    assert report["trajectory"]["cost"]["prompt_tokens"] == (24 if bound else 12)
    assert report["trajectory"]["result"]["status"] == ("needs_human" if bound else "completed")
    if bound:
        assert report["trajectory"]["deliverable_contract"] == json.loads(public_contract.read_text())
        assert report["trajectory"]["deliverable_verification"]["status"] == "failure"
        assert report["trajectory"]["deliverable_verification"]["data"]["contract_binding"] == "run_argument"
    assert report["artifact_inventory"]["status"] == "issues"
    assert report["artifact_inventory"]["official_evaluation"] == "not_run"
    if structured:
        assert report["trajectory"]["task_input"]["source"] == "task.txt"
        assert any("If asked to modify a database" in message["content"]
                   for request in requests for message in request["messages"])
    assert list((workspace / ".praxile/experience/trajectories").glob("*.json"))


def test_existing_state_cannot_enter_frozen_execution(tmp_path):
    (tmp_path / ".praxile").mkdir()
    with pytest.raises(ValueError, match="without .praxile"):
        benchmark_config(tmp_path, model="fixture", base_url="http://localhost/v1", max_steps=2)


@pytest.mark.parametrize("endpoint", ["https://secret@provider/v1", "https://provider/v1?key=secret", "file:///tmp/model"])
def test_worker_rejects_secret_bearing_or_non_http_endpoints(tmp_path, endpoint):
    with pytest.raises(ValueError, match="base_url"):
        benchmark_config(tmp_path, model="fixture", base_url=endpoint, max_steps=2)
