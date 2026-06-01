from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..config import Config
from ..store import ExperienceStore
from ..utils import path_is_relative_to
from .errors import ServiceError


ASSET_LIST_KINDS = ["memory", "skill", "rule", "eval", "failure", "pattern"]


class AssetService:
    def __init__(self, config: Config, store: ExperienceStore):
        self.config = config
        self.store = store

    def list(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        kinds = [kind] if kind else ASSET_LIST_KINDS
        assets: dict[str, dict[str, Any]] = {}
        for item_kind in kinds:
            if not item_kind:
                continue
            for asset in self.store.list_assets(item_kind, include_inactive=True):
                path = str(asset.get("path") or "")
                if path:
                    assets[path] = compact_asset(asset)
        return sorted(assets.values(), key=lambda item: str(item.get("path") or ""))

    def detail(self, path: str) -> dict[str, Any]:
        asset_path = normalize_asset_path(path)
        asset = self.store.get_asset(asset_path)
        if not asset:
            raise ServiceError(404, "Asset not found")
        target = self.config.paths.root / asset_path
        content = ""
        if target.exists() and path_is_relative_to(target, self.config.paths.root):
            content = target.read_text(encoding="utf-8", errors="replace")[:30000]
        return {
            **asset,
            "content": content,
            "usage_history": self.usage(asset_path, limit=20)["usage_history"],
            "graph": self.graph(asset_path, depth=1, limit=60),
        }

    def usage(self, path: str, *, limit: int = 50) -> dict[str, Any]:
        asset_path = normalize_asset_path(path)
        return {
            "path": asset_path,
            "usage_history": self.store.attribution_history_for_asset(asset_path, limit=limit),
        }

    def graph(self, path: str, *, depth: int = 2, limit: int = 100) -> dict[str, Any]:
        return self.store.graph_explain(normalize_asset_path(path), depth=depth, limit=limit)

    def lifecycle(self, path: str, action: str, payload: dict[str, Any], *, source: str = "web_console") -> dict[str, Any]:
        if not payload.get("confirm"):
            raise ServiceError(400, f"`confirm` is required to {action} an asset")
        asset_path = normalize_asset_path(path)
        asset = self.store.get_asset(asset_path)
        if not asset:
            raise ServiceError(404, "Asset not found")
        if action not in {"archive", "deprecate", "reactivate"}:
            raise ServiceError(404, "Asset route not found")
        status = {"archive": "archived", "deprecate": "deprecated", "reactivate": "active"}[action]
        reason = str(payload.get("reason") or f"manual web console {action}").strip()
        replaced_by = payload.get("replaced_by")
        if replaced_by is not None:
            replaced_by = normalize_asset_path(str(replaced_by))
        return self.store.update_asset_status(
            asset_path,
            status=status,
            replaced_by=replaced_by,
            reason=reason,
            source=source,
        )


def normalize_asset_path(value: str) -> str:
    text = unquote(str(value or "").strip())
    if text.startswith(".praxile/"):
        return text
    return f".praxile/{text}"


def compact_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": asset.get("path"),
        "type": asset.get("type"),
        "title": asset.get("title"),
        "status": asset.get("status", "active"),
        "confidence": asset.get("confidence"),
        "usage_count": asset.get("usage_count", 0),
        "positive_outcome_count": asset.get("positive_outcome_count", 0),
        "negative_outcome_count": asset.get("negative_outcome_count", 0),
        "last_used_at": asset.get("last_used_at"),
        "source_task_id": asset.get("source_task_id"),
    }
