from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.request import Request

import pytest

from praxile.config import Config
from praxile.github import GitHubConnector, GitHubIntegrationError, build_pr_comment_body, import_actions_artifacts


class FakeResponse:
    def __init__(self, raw: bytes):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def read(self) -> bytes:
        return self.raw


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_github_pr_comment_uses_issue_comment_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config.load(tmp_path)
    config.data["github"]["repository"] = "Praxile-Alpha/Praxile"
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    calls: list[Request] = []

    def fake_urlopen(request: Request, timeout: int | None = None) -> FakeResponse:
        calls.append(request)
        return FakeResponse(json.dumps({"id": 123, "html_url": "https://example.test/comment"}).encode("utf-8"))

    connector = GitHubConnector(config, urlopen=fake_urlopen)
    body = build_pr_comment_body({"status": "passed", "run_id": "task_1", "reward": 0.8})
    result = connector.create_pr_comment(repo="Praxile-Alpha/Praxile", pr_number=7, body=body)

    assert result["status"] == "published"
    assert calls[0].full_url.endswith("/repos/Praxile-Alpha/Praxile/issues/7/comments")
    assert json.loads(calls[0].data.decode("utf-8"))["body"].startswith("<!-- praxile-report -->")


def test_import_actions_artifacts_extracts_inside_praxile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config.load(tmp_path)
    config.data["github"]["repository"] = "Praxile-Alpha/Praxile"
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    def fake_urlopen(request: Request, timeout: int | None = None) -> FakeResponse:
        if "/actions/runs/42/artifacts" in request.full_url:
            return FakeResponse(
                json.dumps(
                    {
                        "artifacts": [
                            {"id": 99, "name": "praxile-report", "expired": False, "size_in_bytes": 12},
                        ]
                    }
                ).encode("utf-8")
            )
        if "/actions/artifacts/99/zip" in request.full_url:
            return FakeResponse(_zip_bytes({"report.json": "{\"ok\": true}"}))
        raise AssertionError(request.full_url)

    manifest = import_actions_artifacts(
        config,
        {"run_id": "42"},
        connector=GitHubConnector(config, urlopen=fake_urlopen),
    )

    assert manifest["selected_count"] == 1
    assert manifest["artifacts"][0]["files"][0].endswith("report.json")
    assert (tmp_path / manifest["artifacts"][0]["files"][0]).exists()


def test_import_actions_artifacts_blocks_zip_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config.load(tmp_path)
    config.data["github"]["repository"] = "Praxile-Alpha/Praxile"
    monkeypatch.setenv("GITHUB_TOKEN", "secret")

    def fake_urlopen(request: Request, timeout: int | None = None) -> FakeResponse:
        if "/actions/runs/42/artifacts" in request.full_url:
            return FakeResponse(json.dumps({"artifacts": [{"id": 99, "name": "bad", "expired": False}]}).encode("utf-8"))
        return FakeResponse(_zip_bytes({"../escape.txt": "no"}))

    with pytest.raises(GitHubIntegrationError, match="Unsafe artifact path"):
        import_actions_artifacts(config, {"run_id": "42"}, connector=GitHubConnector(config, urlopen=fake_urlopen))
