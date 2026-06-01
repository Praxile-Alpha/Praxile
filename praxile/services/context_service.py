from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..config import Config
from ..constants import DEFAULT_EXCLUDES
from ..github import GitHubConnector, GitHubIntegrationError
from ..inspector import inspect_project
from ..project_map import build_project_map
from ..security import SafetyPolicy
from ..specs import build_spec_context
from ..store import ExperienceStore
from ..utils import new_id, read_json, run_process, shorten, utc_now, write_json


SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".txt"}
CONFIG_FILENAMES = {
    "Cargo.toml",
    "Makefile",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
}
CONFIG_EXTENSIONS = {".json", ".jsonc", ".toml", ".yaml", ".yml"}
TEST_PATH_TERMS = {"test", "tests", "__tests__", "spec", "specs"}
ASSET_LIST_KINDS = ["memory", "skill", "rule", "eval", "failure", "pattern"]


class RepositoryContextService:
    """Builds project-context snapshots without mutating active experience assets."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store
        self.root = config.paths.root
        self.safety = SafetyPolicy(config)

    def status(
        self,
        *,
        refresh_map: bool = False,
        since: str | None = None,
        include_docs: bool = True,
        include_specs: bool = True,
        include_ci: bool = False,
        include_github: bool = False,
        github_fetch: bool = False,
    ) -> dict[str, Any]:
        scan = self._scan_repository()
        profile = inspect_project(self.root)
        spec_context = build_spec_context(self.root)
        project_map = build_project_map(self.config, refresh=refresh_map)
        index = self.store.index_status(scan=False)
        graph = self.store.graph_status()
        runs = self.store.list_history(limit=1000)
        proposals = self.store.list_proposals(status=None, limit=1000)
        pending = [item for item in proposals if item.get("status") == "pending"]
        latest_snapshot = self._latest_context_snapshot()
        active_assets = self._active_assets()
        reflect = self._reflect_summary()
        status = {
            "generated_at": utc_now(),
            "project": {
                "root": str(self.root),
                "state": str(self.config.paths.state),
                "config_exists": self.config.paths.config.exists(),
            },
            "detected": {
                "stacks": profile.stacks,
                "markers": profile.markers,
                "package_manager": profile.package_manager,
                "test_commands": self.config.get("runtime", "default_test_commands", default=[]) or profile.test_commands,
                "missing_tools": profile.missing_tools,
            },
            "repository": scan,
            "sync_scope": {
                "since": since,
                "include_docs": include_docs,
                "include_specs": include_specs,
                "include_ci": include_ci,
                "include_github": include_github,
            },
            "specs": {
                "enabled": spec_context.get("enabled", False),
                "quality_score": spec_context.get("quality_score", 0),
                "quality_label": spec_context.get("quality_label"),
                "spec_files": spec_context.get("spec_files", []),
                "plan_files": spec_context.get("plan_files", []),
                "task_files": spec_context.get("task_files", []),
                "constitution_files": spec_context.get("constitution_files", []),
            },
            "docs": {
                "files": self._docs_files(scan, spec_context),
            },
            "project_map": {
                "total_files": project_map.get("total_files", 0),
                "important_files": project_map.get("important_files", []),
                "high_risk_modules": project_map.get("high_risk_modules", []),
                "cache": project_map.get("cache", {}),
            },
            "experience": {
                "runs": len(runs),
                "recent_failures": self._recent_failures(runs),
                "pending_proposals": len(pending),
                "pending_proposal_sample": [
                    {
                        "proposal_id": item.get("proposal_id"),
                        "type": item.get("type"),
                        "risk_level": item.get("risk_level"),
                        "title": item.get("title"),
                    }
                    for item in pending[:8]
                ],
                "active_assets": len(active_assets),
                "active_asset_sample": active_assets[:8],
                "reflect": reflect,
                "assets_indexed": index.get("assets_indexed", 0),
                "vectors_indexed": index.get("vectors_indexed", 0),
                "pending_index_events": index.get("pending_events", 0),
                "graph_nodes": graph.get("nodes", 0),
                "graph_edges": graph.get("edges", 0),
            },
            "context_juice": self._context_juice(scan),
            "latest_snapshot": latest_snapshot,
        }
        if include_docs:
            status["docs"]["index"] = self._docs_index(scan, spec_context)
        if include_specs:
            status["specs"]["index"] = self._specs_index(spec_context)
        if include_ci:
            status["ci"] = self._ci_index()
        if include_github:
            status["github"] = self._github_context(fetch=github_fetch)
        if since:
            status["repository"]["git"]["since"] = since
            status["repository"]["git"]["commits_since"] = self._git_commits_since(since)
        status["freshness"] = self._freshness(status)
        status["health"] = self._health(status)
        status["recommendations"] = self._recommendations(status)
        return status

    def sync(
        self,
        *,
        write: bool = True,
        refresh_map: bool = True,
        since: str | None = None,
        include_docs: bool = True,
        include_specs: bool = True,
        include_ci: bool = False,
        include_github: bool = False,
        github_fetch: bool = False,
    ) -> dict[str, Any]:
        status = self.status(
            refresh_map=refresh_map,
            since=since,
            include_docs=include_docs,
            include_specs=include_specs,
            include_ci=include_ci,
            include_github=include_github,
            github_fetch=github_fetch,
        )
        snapshot_id = new_id("ctx")
        snapshot = {
            "snapshot_id": snapshot_id,
            "kind": "repository_context_snapshot",
            "created_at": utc_now(),
            "status": status,
        }
        output_path = self._snapshot_dir() / f"{snapshot_id}.json"
        if write:
            snapshot["path"] = output_path.relative_to(self.root).as_posix()
            context_root = self.config.paths.state / "context"
            snapshot["repo_snapshot_path"] = (context_root / "repo_snapshot.json").relative_to(self.root).as_posix()
            git_context = status.get("repository", {}).get("git", {})
            snapshot["recent_commits_path"] = (context_root / "commits" / "recent.json").relative_to(self.root).as_posix()
            snapshot["working_tree_diff_path"] = (context_root / "diffs" / "working_tree.json").relative_to(self.root).as_posix()
            if since:
                snapshot["commits_since_path"] = (context_root / "commits" / "since.json").relative_to(self.root).as_posix()
            write_json(output_path, snapshot)
            write_json(self._snapshot_dir() / "latest.json", snapshot)
            write_json(context_root / "repo_snapshot.json", snapshot)
            write_json(context_root / "snapshots" / f"{snapshot_id}.json", snapshot)
            write_json(
                context_root / "commits" / "recent.json",
                {
                    "generated_at": status.get("generated_at"),
                    "repository_root": str(self.root),
                    "available": bool(git_context.get("available")),
                    "commits": git_context.get("recent_commits", []),
                },
            )
            write_json(context_root / "diffs" / "working_tree.json", git_context.get("working_tree_diff", {}))
            if since:
                write_json(
                    context_root / "commits" / "since.json",
                    {
                        "generated_at": status.get("generated_at"),
                        "since": since,
                        "available": bool(git_context.get("available")),
                        "commits": git_context.get("commits_since", []),
                    },
                )
            if include_docs:
                write_json(context_root / "docs_index.json", status.get("docs", {}).get("index", {}))
            if include_specs:
                write_json(context_root / "specs_index.json", status.get("specs", {}).get("index", {}))
            if include_ci:
                write_json(context_root / "ci" / "index.json", status.get("ci", {}))
            if include_github:
                write_json(context_root / "github" / "context.json", status.get("github", {}))
        else:
            snapshot["path"] = None
        return snapshot

    def _scan_repository(self) -> dict[str, Any]:
        max_files = int(self.config.get("repository_context", "max_files", default=600) or 600)
        max_path_samples = int(self.config.get("repository_context", "max_path_samples", default=80) or 80)
        root = self.root.resolve()
        excluded = set(DEFAULT_EXCLUDES)
        excluded.update(self.config.get("workspace", "copy_excludes", default=[]) or [])
        categories = {
            "source": {"count": 0, "bytes": 0, "paths": []},
            "tests": {"count": 0, "bytes": 0, "paths": []},
            "docs": {"count": 0, "bytes": 0, "paths": []},
            "config": {"count": 0, "bytes": 0, "paths": []},
            "other": {"count": 0, "bytes": 0, "paths": []},
        }
        protected_skipped = 0
        total_files = 0
        total_bytes = 0
        truncated = False

        for current, dirs, filenames in os.walk(root):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if name not in excluded]
            for filename in filenames:
                path = current_path / filename
                try:
                    rel = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    protected_skipped += 1
                    continue
                if any(part in excluded for part in Path(rel).parts):
                    continue
                decision = self.safety.check_path(rel, write=False)
                if not decision.allowed:
                    protected_skipped += 1
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                category = self._category(rel, path)
                total_files += 1
                total_bytes += stat.st_size
                bucket = categories[category]
                bucket["count"] += 1
                bucket["bytes"] += stat.st_size
                if len(bucket["paths"]) < max_path_samples:
                    bucket["paths"].append(rel)
                if total_files >= max_files:
                    truncated = True
                    break
            if truncated:
                break

        git = self._git_status()
        return {
            "scanned_files": total_files,
            "scanned_bytes": total_bytes,
            "protected_skipped": protected_skipped,
            "truncated": truncated,
            "categories": categories,
            "git": git,
        }

    def _category(self, rel: str, path: Path) -> str:
        parts = {part.lower() for part in Path(rel).parts}
        suffix = path.suffix.lower()
        if suffix in DOC_EXTENSIONS or any(term in path.name.lower() for term in ("readme", "roadmap", "spec", "prd")):
            return "docs"
        if parts & TEST_PATH_TERMS or path.name.lower().startswith("test_") or path.name.lower().endswith("_test.py"):
            return "tests"
        if path.name in CONFIG_FILENAMES or suffix in CONFIG_EXTENSIONS:
            return "config"
        if suffix in SOURCE_EXTENSIONS:
            return "source"
        return "other"

    def _git_status(self) -> dict[str, Any]:
        result = run_process(["git", "status", "--short", "--", "."], cwd=self.root, timeout=5)
        if result.returncode != 0:
            return {
                "available": False,
                "dirty": False,
                "changed_files": 0,
                "sample": [],
                "recent_commits": [],
                "working_tree_diff": self._empty_git_diff(available=False, error=result.stderr),
            }
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        log = run_process(["git", "log", "--oneline", "-5", "--", "."], cwd=self.root, timeout=5)
        commits = [line.strip() for line in log.stdout.splitlines() if line.strip()] if log.returncode == 0 else []
        return {
            "available": True,
            "dirty": bool(lines),
            "changed_files": len(lines),
            "sample": lines[:20],
            "recent_commits": commits,
            "working_tree_diff": self._git_recent_diff(),
        }

    def _git_commits_since(self, since: str) -> list[str]:
        result = run_process(["git", "log", "--oneline", f"--since={since}", "-20", "--", "."], cwd=self.root, timeout=5)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def _git_recent_diff(self) -> dict[str, Any]:
        max_chars = max(1000, int(self.config.get("repository_context", "max_diff_chars", default=30000) or 30000))
        stat = run_process(["git", "diff", "--stat", "--", "."], cwd=self.root, timeout=5)
        if stat.returncode != 0:
            return self._empty_git_diff(available=False, error=stat.stderr)
        names = run_process(["git", "diff", "--name-status", "--", "."], cwd=self.root, timeout=5)
        patch = run_process(["git", "diff", "--unified=3", "--", "."], cwd=self.root, timeout=5)
        patch_text = patch.stdout if patch.returncode == 0 else ""
        return {
            "available": True,
            "dirty": bool(stat.stdout.strip() or (names.stdout if names.returncode == 0 else "").strip()),
            "stat": stat.stdout.strip(),
            "name_status": [line for line in names.stdout.splitlines() if line.strip()] if names.returncode == 0 else [],
            "patch_excerpt": shorten(patch_text, max_chars),
            "truncated": len(patch_text) > max_chars,
            "max_chars": max_chars,
            "scoped_to": ".",
        }

    def _empty_git_diff(self, *, available: bool, error: str = "") -> dict[str, Any]:
        return {
            "available": available,
            "dirty": False,
            "stat": "",
            "name_status": [],
            "patch_excerpt": "",
            "truncated": False,
            "error": shorten(error, 500) if error else None,
            "scoped_to": ".",
        }

    def _docs_files(self, scan: dict[str, Any], spec_context: dict[str, Any]) -> list[str]:
        docs = scan.get("categories", {}).get("docs", {}).get("paths", [])
        spec_files = set(spec_context.get("spec_files") or [])
        plan_files = set(spec_context.get("plan_files") or [])
        task_files = set(spec_context.get("task_files") or [])
        constitution_files = set(spec_context.get("constitution_files") or [])
        excluded = spec_files | plan_files | task_files | constitution_files
        return [str(path) for path in docs if str(path) not in excluded][:40]

    def _docs_index(self, scan: dict[str, Any], spec_context: dict[str, Any]) -> dict[str, Any]:
        entries = [self._file_index_entry(rel) for rel in self._docs_files(scan, spec_context)]
        entries = [entry for entry in entries if entry]
        return {
            "generated_at": utc_now(),
            "count": len(entries),
            "files": entries,
        }

    def _specs_index(self, spec_context: dict[str, Any]) -> dict[str, Any]:
        paths: list[str] = []
        for key in ("spec_files", "plan_files", "task_files", "constitution_files"):
            for item in spec_context.get(key) or []:
                if str(item) not in paths:
                    paths.append(str(item))
        entries = [self._file_index_entry(rel) for rel in paths]
        entries = [entry for entry in entries if entry]
        return {
            "generated_at": utc_now(),
            "quality_score": spec_context.get("quality_score", 0),
            "quality_label": spec_context.get("quality_label"),
            "count": len(entries),
            "files": entries,
        }

    def _file_index_entry(self, rel: str) -> dict[str, Any] | None:
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root.resolve())
            stat = path.stat()
        except (ValueError, OSError):
            return None
        title = ""
        headings: list[str] = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[:120]:
                stripped = line.strip()
                if stripped.startswith("#"):
                    headings.append(stripped[:160])
                    if not title:
                        title = stripped.lstrip("#").strip()
                if len(headings) >= 8:
                    break
        except OSError:
            pass
        return {
            "path": rel,
            "title": title or path.stem,
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "headings": headings,
        }

    def _ci_index(self) -> dict[str, Any]:
        roots = [
            self.config.paths.state / "experience" / "ci",
            self.config.paths.state / "experience" / "reflect" / "ci",
        ]
        reports: list[dict[str, Any]] = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)[:80]:
                data = _safe_read_json(path, {})
                if not isinstance(data, dict):
                    continue
                reports.append(
                    {
                        "path": path.relative_to(self.root).as_posix(),
                        "kind": data.get("kind"),
                        "status": data.get("status") or (data.get("ci") or {}).get("passed"),
                        "created_at": data.get("created_at") or data.get("generated_at"),
                        "summary": data.get("summary") or data.get("title"),
                    }
                )
        return {
            "generated_at": utc_now(),
            "report_count": len(reports),
            "reports": reports[:40],
        }

    def _github_context(self, *, fetch: bool = False) -> dict[str, Any]:
        fetch = bool(fetch or self.config.get("github", "sync_network", default=False))
        base = {
            "generated_at": utc_now(),
            "repository": self.config.get("github", "repository", default=None) or os.environ.get("GITHUB_REPOSITORY"),
            "default_pr_number": self.config.get("github", "default_pr_number", default=None) or os.environ.get("PR_NUMBER"),
            "actions_run_id": os.environ.get("GITHUB_RUN_ID"),
            "sha": os.environ.get("GITHUB_SHA"),
            "ref": os.environ.get("GITHUB_REF"),
            "event_name": os.environ.get("GITHUB_EVENT_NAME"),
            "network_fetch": fetch,
            "pull_requests": [],
            "issues": [],
            "errors": [],
        }
        connector = GitHubConnector(self.config)
        try:
            base["connector"] = connector.context()
        except GitHubIntegrationError as exc:
            base["errors"].append(f"connector_context: {exc}")
        if not fetch:
            base["network_fetch_reason"] = "disabled; pass --github-online or set github.sync_network=true to fetch PR/issue summaries"
            return base
        try:
            repo = connector.repository(str(base["repository"]) if base.get("repository") else None)
            if not repo:
                base["errors"].append("repository not configured")
                return base
            limit = max(1, int(self.config.get("github", "max_sync_items", default=20) or 20))
            base["repository"] = repo
            base["pull_requests"] = connector.list_pull_requests(repo=repo, limit=limit)
            base["issues"] = connector.list_issues(repo=repo, limit=limit)
        except GitHubIntegrationError as exc:
            base["errors"].append(str(exc))
        return base

    def _active_assets(self) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for kind in ASSET_LIST_KINDS:
            for asset in self.store.list_assets(kind, include_inactive=False):
                assets.append(
                    {
                        "path": asset.get("path"),
                        "type": asset.get("type"),
                        "title": asset.get("title"),
                        "usage_count": asset.get("usage_count", 0),
                        "last_used_at": asset.get("last_used_at"),
                    }
                )
        return sorted(assets, key=lambda item: str(item.get("path") or ""))

    def _recent_failures(self, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        failed: list[dict[str, Any]] = []
        for row in runs:
            status = str(row.get("status") or "")
            if status not in {"failed", "needs_human", "cancelled"}:
                continue
            failed.append(
                {
                    "task_id": row.get("task_id"),
                    "status": status,
                    "task": row.get("user_task"),
                    "created_at": row.get("created_at"),
                }
            )
        return failed[:8]

    def _reflect_summary(self) -> dict[str, Any]:
        root = self.config.paths.state / "experience" / "reflect"
        reports: list[dict[str, Any]] = []
        if root.exists():
            for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
                data = _safe_read_json(path, {})
                if not isinstance(data, dict):
                    continue
                findings = data.get("findings") if isinstance(data.get("findings"), list) else []
                reports.append(
                    {
                        "reflect_id": data.get("reflect_id") or path.stem,
                        "created_at": data.get("created_at"),
                        "finding_count": len(findings),
                    }
                )
        return {
            "latest": reports[0] if reports else None,
            "report_count": len(reports),
            "latest_finding_count": reports[0]["finding_count"] if reports else 0,
        }

    def _freshness(self, status: dict[str, Any]) -> dict[str, Any]:
        latest = status.get("latest_snapshot") or {}
        if not latest:
            return {
                "level": "never_synced",
                "last_sync_at": None,
                "message": "No repository context snapshot has been written yet.",
            }
        dirty = bool(status.get("repository", {}).get("git", {}).get("dirty"))
        return {
            "level": "dirty_after_sync" if dirty else "synced",
            "last_sync_at": latest.get("created_at"),
            "message": "Repository has uncommitted changes after the last context sync." if dirty else "Repository context snapshot is available.",
        }

    def _context_juice(self, scan: dict[str, Any]) -> dict[str, Any]:
        raw_chars = int(scan.get("scanned_bytes", 0))
        categories = scan.get("categories", {})
        summary_bits: list[str] = []
        for name in ("source", "tests", "docs", "config"):
            bucket = categories.get(name, {})
            summary_bits.append(f"{name}:{bucket.get('count', 0)}")
        compressed_chars = len("; ".join(summary_bits))
        savings = 0.0 if raw_chars <= 0 else max(0.0, min(1.0, 1 - (compressed_chars / raw_chars)))
        latest = _safe_read_json(self.config.paths.state / "context" / "summaries" / "latest.json", {})
        latest_summary = None
        if isinstance(latest, dict) and latest.get("kind") == "context_juice":
            latest_summary = {
                "compression_id": latest.get("compression_id"),
                "role": latest.get("role"),
                "source_type": latest.get("source_type"),
                "ratio": latest.get("ratio"),
                "estimated_savings": latest.get("estimated_savings"),
                "created_at": latest.get("created_at"),
                "markdown_path": latest.get("markdown_path"),
            }
        return {
            "raw_chars_estimate": raw_chars,
            "compressed_chars_estimate": compressed_chars,
            "estimated_savings": round(savings, 4),
            "strategy": "category-map-plus-sampled-paths",
            "latest_summary": latest_summary,
        }

    def _health(self, status: dict[str, Any]) -> dict[str, Any]:
        repository = status["repository"]
        categories = repository.get("categories", {})
        experience = status["experience"]
        signals = {
            "config_exists": bool(status["project"].get("config_exists")),
            "has_source": int(categories.get("source", {}).get("count", 0) or 0) > 0,
            "has_tests": int(categories.get("tests", {}).get("count", 0) or 0) > 0,
            "has_docs_or_specs": int(categories.get("docs", {}).get("count", 0) or 0) > 0,
            "has_runs": int(experience.get("runs", 0) or 0) > 0,
            "has_indexed_assets": int(experience.get("assets_indexed", 0) or 0) > 0,
            "has_graph": int(experience.get("graph_nodes", 0) or 0) > 0,
            "index_queue_clear": int(experience.get("pending_index_events", 0) or 0) == 0,
            "has_context_snapshot": bool(status.get("latest_snapshot")),
        }
        weights = {
            "config_exists": 0.10,
            "has_source": 0.14,
            "has_tests": 0.12,
            "has_docs_or_specs": 0.11,
            "has_runs": 0.11,
            "has_indexed_assets": 0.12,
            "has_graph": 0.10,
            "index_queue_clear": 0.08,
            "has_context_snapshot": 0.12,
        }
        score = round(sum(weights[key] for key, ok in signals.items() if ok), 3)
        if score >= 0.82:
            level = "strong"
        elif score >= 0.60:
            level = "healthy"
        elif score >= 0.35:
            level = "partial"
        else:
            level = "weak"
        return {"score": score, "level": level, "signals": signals}

    def _recommendations(self, status: dict[str, Any]) -> list[dict[str, str]]:
        recommendations: list[dict[str, str]] = []
        health = status.get("health", {})
        signals = health.get("signals", {})
        if not signals.get("has_tests"):
            recommendations.append({"kind": "verification", "message": "No test files were found in the sampled repository context."})
        if not signals.get("has_docs_or_specs"):
            recommendations.append({"kind": "spec", "message": "Add README, spec, PRD, or roadmap context so future runs can ground decisions."})
        if not signals.get("has_indexed_assets"):
            recommendations.append({"kind": "experience", "message": "Run a task or accept proposals so Praxile has project-local experience to retrieve."})
        if status["experience"].get("pending_index_events"):
            recommendations.append({"kind": "index", "message": "Run `praxile index update` to process pending experience index events."})
        if status["repository"].get("git", {}).get("dirty"):
            recommendations.append({"kind": "git", "message": "Review uncommitted changes before broad agent runs."})
        if not status.get("latest_snapshot"):
            recommendations.append({"kind": "sync", "message": "Run `praxile sync` to write a repository context snapshot."})
        return recommendations

    def _latest_context_snapshot(self) -> dict[str, Any] | None:
        path = self._snapshot_dir() / "latest.json"
        data = _safe_read_json(path, None)
        if not isinstance(data, dict):
            return None
        return {
            "snapshot_id": data.get("snapshot_id"),
            "created_at": data.get("created_at"),
            "path": path.relative_to(self.root).as_posix(),
        }

    def _snapshot_dir(self) -> Path:
        return self.config.paths.state / "experience" / "context"


def format_context_status(payload: dict[str, Any]) -> str:
    status = payload.get("status", payload)
    health = status.get("health", {})
    repo = status.get("repository", {})
    categories = repo.get("categories", {})
    experience = status.get("experience", {})
    detected = status.get("detected", {})
    specs = status.get("specs", {})
    freshness = status.get("freshness", {})
    lines = [
        "Repository context:",
        f"- health: {health.get('level', 'unknown')} ({health.get('score', 0)})",
        f"- freshness: {freshness.get('level', 'unknown')} last_sync={freshness.get('last_sync_at') or 'never'}",
        f"- scanned files: {repo.get('scanned_files', 0)}"
        + (" (truncated)" if repo.get("truncated") else ""),
        f"- git dirty files: {repo.get('git', {}).get('changed_files', 0)}",
        f"- stacks: {', '.join(detected.get('stacks') or []) or '(none)'}",
        f"- detected tests: {', '.join(detected.get('test_commands') or []) or '(none)'}",
        f"- spec files: {len(specs.get('spec_files') or [])}",
        f"- runs: {experience.get('runs', 0)}",
        f"- pending proposals: {experience.get('pending_proposals', 0)}",
        f"- active assets: {experience.get('active_assets', 0)}",
        f"- indexed assets: {experience.get('assets_indexed', 0)}",
        f"- graph: {experience.get('graph_nodes', 0)} nodes / {experience.get('graph_edges', 0)} edges",
    ]
    for name in ("source", "tests", "docs", "config"):
        bucket = categories.get(name, {})
        lines.append(f"- {name}: {bucket.get('count', 0)} files")
    recommendations = status.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations:")
        lines.extend(f"- {item.get('kind')}: {item.get('message')}" for item in recommendations[:8])
    path = payload.get("path")
    if path:
        lines.append(f"Snapshot: {path}")
    return "\n".join(lines)


def _safe_read_json(path: Path, default: Any = None) -> Any:
    try:
        return read_json(path, default)
    except (OSError, ValueError):
        return default
