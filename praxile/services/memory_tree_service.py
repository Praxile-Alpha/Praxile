from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..config import Config
from ..store import ExperienceStore
from ..utils import new_id, read_json, shorten, slugify, utc_now, write_json
from .context_service import RepositoryContextService


TREE_ASSET_KINDS = ["memory", "skill", "rule", "eval", "failure", "pattern"]


class RepositoryMemoryTreeService:
    """Builds a human-readable repository memory tree from context and approved assets."""

    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def build(self, *, module: str | None = None, recent: str | None = None, write: bool = True) -> dict[str, Any]:
        context = RepositoryContextService(self.config, self.store).status(refresh_map=False)
        assets = self._assets()
        runs = self.store.list_history(limit=30)
        modules = self._modules(context, assets)
        selected_modules = {module: modules.get(module, {})} if module else modules
        tree_id = new_id("tree")
        links = self._links(selected_modules, assets, runs)
        markdown = self._format_markdown(
            tree_id=tree_id,
            context=context,
            modules=selected_modules,
            assets=assets,
            runs=runs,
            module=module,
            recent=recent,
        )
        payload = {
            "tree_id": tree_id,
            "kind": "repository_memory_tree",
            "created_at": utc_now(),
            "module_filter": module,
            "recent": recent,
            "module_count": len(selected_modules),
            "asset_count": len(assets),
            "run_count": len(runs),
            "modules": selected_modules,
            "links": links,
            "markdown": markdown,
        }
        if write:
            root = self._tree_dir()
            root.mkdir(parents=True, exist_ok=True)
            index_path = root / "index.md"
            json_path = root / "tree.json"
            index_path.write_text(markdown, encoding="utf-8")
            write_json(json_path, {**payload, "markdown_path": index_path.relative_to(self.config.paths.root).as_posix()})
            self._write_module_pages(root, modules, assets)
            written_paths = self._write_object_views(root, context, assets, runs)
            payload["path"] = json_path.relative_to(self.config.paths.root).as_posix()
            payload["markdown_path"] = index_path.relative_to(self.config.paths.root).as_posix()
            payload["written_paths"] = [index_path.relative_to(self.config.paths.root).as_posix(), *written_paths]
        return payload

    def latest(self) -> dict[str, Any] | None:
        path = self._tree_dir() / "tree.json"
        data = read_json(path, None)
        return data if isinstance(data, dict) else None

    def _assets(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for kind in TREE_ASSET_KINDS:
            for asset in self.store.list_assets(kind, include_inactive=False):
                items.append(
                    {
                        "path": asset.get("path"),
                        "type": asset.get("type") or kind,
                        "title": asset.get("title") or Path(str(asset.get("path") or "")).stem,
                        "tags": asset.get("tags") or [],
                        "usage_count": asset.get("usage_count", 0),
                        "last_used_at": asset.get("last_used_at"),
                    }
                )
        return sorted(items, key=lambda item: (str(item.get("type") or ""), str(item.get("path") or "")))

    def _modules(self, context: dict[str, Any], assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"paths": [], "asset_paths": [], "asset_types": Counter(), "risk_notes": []})
        categories = context.get("repository", {}).get("categories", {}) if isinstance(context.get("repository"), dict) else {}
        for category_name in ("source", "tests", "docs", "config"):
            paths = (categories.get(category_name) or {}).get("paths") or []
            for rel in paths:
                module = self._module_for_path(str(rel))
                bucket = buckets[module]
                if len(bucket["paths"]) < 40:
                    bucket["paths"].append(str(rel))
        for asset in assets:
            asset_path = str(asset.get("path") or "")
            module = self._module_for_asset(asset)
            bucket = buckets[module]
            if len(bucket["asset_paths"]) < 40:
                bucket["asset_paths"].append(asset_path)
            bucket["asset_types"][str(asset.get("type") or "asset")] += 1
        high_risk_modules = context.get("project_map", {}).get("high_risk_modules", []) if isinstance(context.get("project_map"), dict) else []
        for item in high_risk_modules or []:
            if isinstance(item, dict):
                module_name = str(item.get("path") or item.get("module") or "project")
                buckets[self._module_for_path(module_name)]["risk_notes"].append(item)
        result: dict[str, dict[str, Any]] = {}
        for name, bucket in sorted(buckets.items()):
            asset_types = bucket.pop("asset_types")
            result[name] = {**bucket, "asset_type_counts": dict(asset_types)}
        return result

    def _module_for_asset(self, asset: dict[str, Any]) -> str:
        path = str(asset.get("path") or "")
        tags = [str(tag) for tag in asset.get("tags") or [] if tag]
        for tag in tags:
            if tag.startswith("module:"):
                return slugify(tag.split(":", 1)[1], max_length=40)
        return self._module_for_path(path)

    def _module_for_path(self, rel: str) -> str:
        parts = [part for part in Path(rel).parts if part not in {".praxile", "praxile"}]
        if not parts:
            return "project"
        if parts[0] in {"src", "lib", "app", "packages", "apps", "tests", "docs"} and len(parts) > 1:
            return slugify(parts[1], max_length=40)
        return slugify(parts[0], max_length=40)

    def _format_markdown(
        self,
        *,
        tree_id: str,
        context: dict[str, Any],
        modules: dict[str, dict[str, Any]],
        assets: list[dict[str, Any]],
        runs: list[dict[str, Any]],
        module: str | None,
        recent: str | None,
    ) -> str:
        health = context.get("health", {})
        freshness = context.get("freshness", {})
        specs = context.get("specs", {})
        lines = [
            f"# Repository Memory Tree {tree_id}",
            "",
            f"- generated_at: `{utc_now()}`",
            f"- health: `{health.get('level', 'unknown')}` ({health.get('score', 0)})",
            f"- last_sync: `{freshness.get('last_sync_at') or 'never'}`",
            f"- module_filter: `{module or 'all'}`",
            f"- recent: `{recent or 'default'}`",
            "",
            "## Specs",
            "",
        ]
        spec_files = specs.get("spec_files") or []
        lines.extend(f"- `{item}`" for item in spec_files[:20])
        if not spec_files:
            lines.append("- No spec files detected yet.")
        lines.extend(["", "## Modules", ""])
        for name, bucket in modules.items():
            lines.append(f"### {name}")
            paths = bucket.get("paths") or []
            asset_paths = bucket.get("asset_paths") or []
            lines.append(f"- paths: {len(paths)}")
            lines.append(f"- active assets: {len(asset_paths)}")
            if bucket.get("asset_type_counts"):
                type_bits = ", ".join(f"{key}:{value}" for key, value in sorted(bucket["asset_type_counts"].items()))
                lines.append(f"- asset types: {type_bits}")
            if paths:
                lines.append("- sample paths:")
                lines.extend(f"  - `{path}`" for path in paths[:8])
            if asset_paths:
                lines.append("- assets:")
                lines.extend(f"  - `{path}`" for path in asset_paths[:8])
            lines.append("")
        lines.extend(["## Active Experience Assets", ""])
        if assets:
            for asset in assets[:30]:
                lines.append(f"- `{asset.get('path')}` [{asset.get('type')}] {asset.get('title')}")
        else:
            lines.append("- No active assets yet.")
        lines.extend(["", "## Recent Runs", ""])
        if runs:
            for run in runs[:12]:
                task = shorten(str(run.get("user_task") or run.get("task") or ""), 120).replace("\n", " ")
                lines.append(f"- `{run.get('task_id')}` {run.get('status')} reward={run.get('reward')} {task}")
        else:
            lines.append("- No runs recorded yet.")
        return "\n".join(lines).rstrip() + "\n"

    def _links(self, modules: dict[str, dict[str, Any]], assets: list[dict[str, Any]], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = [
            {
                "kind": "tree",
                "label": "Memory tree index",
                "ref": ".praxile/context/tree/index.md",
                "file": ".praxile/context/tree/index.md",
                "href": "#/context/tree",
            },
            {
                "kind": "context",
                "label": "Repository snapshot",
                "ref": ".praxile/context/repo_snapshot.json",
                "file": ".praxile/context/repo_snapshot.json",
                "href": "#/context/snapshot",
            },
        ]
        for module_name in sorted(modules):
            file_path = f".praxile/context/tree/modules/{slugify(module_name)}.md"
            links.append(
                {
                    "kind": "module",
                    "label": f"Module: {module_name}",
                    "ref": module_name,
                    "file": file_path,
                    "href": f"#/context/tree/module/{quote(module_name, safe='')}",
                }
            )
        for asset in assets[:80]:
            asset_path = str(asset.get("path") or "")
            if not asset_path:
                continue
            links.append(
                {
                    "kind": "asset",
                    "label": str(asset.get("title") or asset_path),
                    "ref": asset_path,
                    "file": asset_path,
                    "href": f"#/assets/{quote(asset_path, safe='')}",
                    "asset_type": asset.get("type"),
                }
            )
        for run in runs[:30]:
            task_id = str(run.get("task_id") or "")
            if not task_id:
                continue
            links.append(
                {
                    "kind": "run",
                    "label": shorten(str(run.get("user_task") or run.get("task") or task_id), 80),
                    "ref": task_id,
                    "href": f"#/runs/{quote(task_id, safe='')}",
                    "status": run.get("status"),
                    "reward": run.get("reward"),
                }
            )
        return links

    def _write_module_pages(self, root: Path, modules: dict[str, dict[str, Any]], assets: list[dict[str, Any]]) -> None:
        module_dir = root / "modules"
        module_dir.mkdir(parents=True, exist_ok=True)
        assets_by_path = {str(asset.get("path") or ""): asset for asset in assets}
        for name, bucket in modules.items():
            lines = [f"# Module: {name}", "", "## Paths", ""]
            lines.extend(f"- `{path}`" for path in (bucket.get("paths") or [])[:80])
            if not bucket.get("paths"):
                lines.append("- No sampled paths.")
            lines.extend(["", "## Assets", ""])
            for asset_path in (bucket.get("asset_paths") or [])[:80]:
                asset = assets_by_path.get(str(asset_path), {})
                lines.append(f"- `{asset_path}` [{asset.get('type', 'asset')}] {asset.get('title', '')}")
            if not bucket.get("asset_paths"):
                lines.append("- No active assets.")
            (module_dir / f"{slugify(name)}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _write_object_views(
        self,
        root: Path,
        context: dict[str, Any],
        assets: list[dict[str, Any]],
        runs: list[dict[str, Any]],
    ) -> list[str]:
        written: list[str] = []
        specs_dir = root / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)
        spec_lines = ["# Specs", ""]
        specs = context.get("specs", {})
        for key in ("spec_files", "plan_files", "task_files", "constitution_files"):
            values = specs.get(key) or []
            if values:
                spec_lines.extend([f"## {key}", ""])
                spec_lines.extend(f"- `{item}`" for item in values)
                spec_lines.append("")
        if len(spec_lines) <= 2:
            spec_lines.append("- No spec files detected yet.")
        spec_path = specs_dir / "index.md"
        spec_path.write_text("\n".join(spec_lines).rstrip() + "\n", encoding="utf-8")
        written.append(spec_path.relative_to(self.config.paths.root).as_posix())

        failures_dir = root / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        failure_lines = ["# Failures", ""]
        recent_failures = context.get("experience", {}).get("recent_failures", [])
        failure_assets = [asset for asset in assets if str(asset.get("type") or "") in {"failure", "pattern"} or "failure" in str(asset.get("path") or "")]
        if recent_failures:
            failure_lines.extend(["## Recent Failed Runs", ""])
            failure_lines.extend(f"- `{item.get('task_id')}` {item.get('status')} {item.get('task') or ''}" for item in recent_failures)
            failure_lines.append("")
        if failure_assets:
            failure_lines.extend(["## Failure Assets", ""])
            failure_lines.extend(f"- `{asset.get('path')}` {asset.get('title') or ''}" for asset in failure_assets[:80])
        if len(failure_lines) <= 2:
            failure_lines.append("- No failure runs or failure assets detected yet.")
        failure_path = failures_dir / "index.md"
        failure_path.write_text("\n".join(failure_lines).rstrip() + "\n", encoding="utf-8")
        written.append(failure_path.relative_to(self.config.paths.root).as_posix())

        decisions_dir = root / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        decision_assets = [asset for asset in assets if "decision" in str(asset.get("path") or "").lower() or "boundary" in str(asset.get("path") or "").lower()]
        decision_lines = ["# Decisions", ""]
        if decision_assets:
            decision_lines.extend(f"- `{asset.get('path')}` [{asset.get('type')}] {asset.get('title') or ''}" for asset in decision_assets[:80])
        else:
            decision_lines.append("- No decision or boundary assets detected yet.")
        decision_path = decisions_dir / "architecture-boundaries.md"
        decision_path.write_text("\n".join(decision_lines).rstrip() + "\n", encoding="utf-8")
        written.append(decision_path.relative_to(self.config.paths.root).as_posix())

        timeline_dir = root / "timelines"
        timeline_dir.mkdir(parents=True, exist_ok=True)
        by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run in runs:
            created = str(run.get("created_at") or run.get("start_time") or "unknown")
            key = created[:7] if len(created) >= 7 else "unknown"
            by_month[key].append(run)
        if not by_month:
            by_month["unknown"] = []
        for month, month_runs in sorted(by_month.items(), reverse=True):
            lines = [f"# Timeline {month}", ""]
            if month_runs:
                for run in month_runs:
                    task = shorten(str(run.get("user_task") or run.get("task") or ""), 160).replace("\n", " ")
                    lines.append(f"- `{run.get('task_id')}` {run.get('status')} reward={run.get('reward')} {task}")
            else:
                lines.append("- No runs recorded yet.")
            path = timeline_dir / f"{slugify(month)}.md"
            path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            written.append(path.relative_to(self.config.paths.root).as_posix())
        return written

    def _tree_dir(self) -> Path:
        return self.config.paths.state / "context" / "tree"
