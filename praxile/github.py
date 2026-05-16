from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Config
from .utils import new_id, path_is_relative_to, utc_now, write_json


class GitHubIntegrationError(RuntimeError):
    pass


UrlOpen = Callable[[urllib.request.Request, Any], Any]


class GitHubConnector:
    def __init__(self, config: Config, *, urlopen: UrlOpen | None = None):
        self.config = config
        self.api_base_url = str(config.get("github", "api_base_url", default="https://api.github.com") or "https://api.github.com").rstrip("/")
        self.api_version = str(config.get("github", "api_version", default="2026-03-10") or "2026-03-10")
        self.token_env = str(config.get("github", "token_env", default="GITHUB_TOKEN") or "GITHUB_TOKEN")
        self.timeout_seconds = int(config.get("github", "timeout_seconds", default=20) or 20)
        self.urlopen = urlopen or _default_urlopen

    def context(self) -> dict[str, Any]:
        token = os.environ.get(self.token_env)
        return {
            "enabled": bool(self.config.get("github", "enabled", default=False)),
            "api_base_url": self.api_base_url,
            "api_version": self.api_version,
            "token_env": self.token_env,
            "token_status": "configured" if token else "missing",
            "repository": self.repository(),
            "default_pr_number": self.default_pr_number(),
            "actions_run_id": os.environ.get("GITHUB_RUN_ID"),
        }

    def repository(self, override: str | None = None) -> str | None:
        repo = override or self.config.get("github", "repository", default=None) or os.environ.get("GITHUB_REPOSITORY")
        if repo is None:
            return None
        repo = str(repo).strip()
        if not repo:
            return None
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
            raise GitHubIntegrationError("GitHub repository must be in owner/repo form")
        return repo

    def default_pr_number(self) -> int | None:
        raw = self.config.get("github", "default_pr_number", default=None)
        if raw:
            return _positive_int(raw, "default_pr_number")
        ref = os.environ.get("GITHUB_REF") or ""
        match = re.match(r"^refs/pull/(\d+)/(merge|head)$", ref)
        return int(match.group(1)) if match else None

    def issue_comment_url(self, repo: str, issue_number: int) -> str:
        owner, name = repo.split("/", 1)
        return f"{self.api_base_url}/repos/{owner}/{name}/issues/{issue_number}/comments"

    def workflow_artifacts_url(self, repo: str, run_id: str | int) -> str:
        owner, name = repo.split("/", 1)
        return f"{self.api_base_url}/repos/{owner}/{name}/actions/runs/{run_id}/artifacts?per_page=100"

    def artifact_zip_url(self, repo: str, artifact_id: str | int) -> str:
        owner, name = repo.split("/", 1)
        return f"{self.api_base_url}/repos/{owner}/{name}/actions/artifacts/{artifact_id}/zip"

    def create_pr_comment(self, *, repo: str, pr_number: int, body: str) -> dict[str, Any]:
        if not body.strip():
            raise GitHubIntegrationError("PR comment body is required")
        payload = self._request_json(
            "POST",
            self.issue_comment_url(repo, pr_number),
            payload={"body": body},
            require_token=True,
        )
        return {
            "repository": repo,
            "pr_number": pr_number,
            "status": "published",
            "comment_id": payload.get("id"),
            "html_url": payload.get("html_url"),
            "api_url": payload.get("url"),
            "created_at": payload.get("created_at"),
        }

    def list_workflow_artifacts(self, *, repo: str, run_id: str | int) -> list[dict[str, Any]]:
        payload = self._request_json("GET", self.workflow_artifacts_url(repo, run_id), require_token=True)
        artifacts = payload.get("artifacts") if isinstance(payload, dict) else []
        return [item for item in artifacts if isinstance(item, dict)]

    def download_artifact_zip(self, *, repo: str, artifact_id: str | int) -> bytes:
        return self._request_bytes("GET", self.artifact_zip_url(repo, artifact_id), require_token=True)

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        require_token: bool = False,
    ) -> dict[str, Any]:
        raw = self._request_bytes(method, url, payload=payload, require_token=require_token)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise GitHubIntegrationError(f"GitHub returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise GitHubIntegrationError("GitHub response must be a JSON object")
        return data

    def _request_bytes(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        require_token: bool = False,
    ) -> bytes:
        token = os.environ.get(self.token_env)
        if require_token and not token:
            raise GitHubIntegrationError(f"Missing GitHub token env var: {self.token_env}")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "praxile-local-gateway",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.urlopen(request, self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise GitHubIntegrationError(f"GitHub HTTP {exc.code}: {detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise GitHubIntegrationError(f"GitHub request failed: {exc}") from exc


def build_pr_comment_body(report: dict[str, Any], *, marker: str = "<!-- praxile-report -->") -> str:
    status = report.get("status") or "unknown"
    lines = [
        marker,
        "## Praxile Report",
        "",
        f"- Status: `{status}`",
        f"- Run: `{report.get('run_id') or report.get('task_id') or 'unknown'}`",
        f"- Reward: `{report.get('reward')}`",
        f"- Proposals: `{report.get('proposal_count', 0)}`",
        f"- Silent failures: `{report.get('silent_failure_count', 0)}`",
    ]
    summary = str(report.get("summary") or "").strip()
    if summary:
        lines.extend(["", summary[:2000]])
    return "\n".join(lines).strip() + "\n"


def import_actions_artifacts(config: Config, payload: dict[str, Any], *, connector: GitHubConnector | None = None) -> dict[str, Any]:
    connector = connector or GitHubConnector(config)
    repo = connector.repository(payload.get("repository") if isinstance(payload.get("repository"), str) else None)
    if not repo:
        raise GitHubIntegrationError("GitHub repository is required")
    run_id = str(payload.get("run_id") or os.environ.get("GITHUB_RUN_ID") or "").strip()
    if not run_id:
        raise GitHubIntegrationError("GitHub Actions run_id is required")
    artifacts = connector.list_workflow_artifacts(repo=repo, run_id=run_id)
    selected = _select_artifacts(artifacts, payload)
    import_id = new_id("gha")
    output_root = _artifact_import_root(config, import_id)
    manifest = {
        "import_id": import_id,
        "kind": "github_actions_artifact_import",
        "created_at": utc_now(),
        "repository": repo,
        "run_id": run_id,
        "selected_count": len(selected),
        "artifacts": [],
    }
    preview_only = bool(payload.get("preview_only", False))
    if not preview_only:
        output_root.mkdir(parents=True, exist_ok=True)
    for artifact in selected:
        row = {
            "id": artifact.get("id"),
            "name": artifact.get("name"),
            "size_in_bytes": artifact.get("size_in_bytes"),
            "expired": artifact.get("expired"),
            "archive_download_url": artifact.get("archive_download_url"),
            "files": [],
        }
        if not preview_only:
            raw = connector.download_artifact_zip(repo=repo, artifact_id=artifact["id"])
            artifact_dir = output_root / _safe_slug(str(artifact.get("name") or artifact.get("id") or "artifact"))
            row["files"] = _extract_zip_safely(raw, artifact_dir, config.paths.root)
        manifest["artifacts"].append(row)
    if not preview_only:
        manifest_path = output_root / "manifest.json"
        write_json(manifest_path, manifest)
        manifest["path"] = str(manifest_path.relative_to(config.paths.root))
    return manifest


def _select_artifacts(artifacts: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_ids = {_positive_int(item, "artifact_id") for item in payload.get("artifact_ids") or []}
    raw_names = {str(item) for item in payload.get("artifact_names") or [] if str(item).strip()}
    include_expired = bool(payload.get("include_expired", False))
    max_artifacts = max(1, min(20, int(payload.get("max_artifacts") or 10)))
    selected = []
    for artifact in artifacts:
        artifact_id = artifact.get("id")
        name = str(artifact.get("name") or "")
        if artifact.get("expired") and not include_expired:
            continue
        if raw_ids and artifact_id not in raw_ids:
            continue
        if raw_names and name not in raw_names:
            continue
        selected.append(artifact)
        if len(selected) >= max_artifacts:
            break
    return selected


def _artifact_import_root(config: Config, import_id: str) -> Path:
    rel = str(config.get("github", "artifact_import_dir", default=".praxile/experience/ci/imported-artifacts") or ".praxile/experience/ci/imported-artifacts")
    root = (config.paths.root / rel / import_id).resolve()
    state = config.paths.state.resolve()
    if not path_is_relative_to(root, state):
        raise GitHubIntegrationError("github.artifact_import_dir must stay inside .praxile")
    return root


def _extract_zip_safely(raw: bytes, artifact_dir: Path, project_root: Path) -> list[str]:
    artifact_dir = artifact_dir.resolve()
    state_root = (project_root / ".praxile").resolve()
    if not path_is_relative_to(artifact_dir, state_root):
        raise GitHubIntegrationError("Artifact import target must stay inside .praxile")
    written: list[str] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = (artifact_dir / info.filename).resolve()
            if not path_is_relative_to(target, artifact_dir):
                raise GitHubIntegrationError(f"Unsafe artifact path in zip: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                destination.write(source.read())
            written.append(str(target.relative_to(project_root)))
    return written


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GitHubIntegrationError(f"{label} must be a positive integer") from exc
    if number <= 0:
        raise GitHubIntegrationError(f"{label} must be a positive integer")
    return number


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return slug[:80] or "artifact"


def _default_urlopen(request: urllib.request.Request, timeout: int | None = None) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)
