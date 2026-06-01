from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class AssetsStoreMixin:
    def list_history(
        self,
        limit: int = 20,
        *,
        status: str | None = None,
        query: str | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query:
            clauses.append("user_task LIKE ?")
            params.append(f"%{query}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([max(1, int(limit or 20)), max(0, int(offset or 0))])
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]
    def list_assets(self, kind: str, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        asset_types = _asset_type_filter([kind]) or [kind]
        status_clause = "" if include_inactive else " AND status = 'active'"
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM assets WHERE type IN (%s)%s ORDER BY path ASC"
                % (",".join("?" for _ in asset_types), status_clause),
                asset_types,
            ).fetchall()
        return [self._asset_row_with_lifecycle(row) for row in rows]
    def get_asset(self, path: str) -> dict[str, Any] | None:
        self._init_db()
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE path = ?", (path,)).fetchone()
        return self._asset_row_with_lifecycle(row) if row else None
    def _asset_row_with_lifecycle(self, row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        sidecar = self._asset_metadata_sidecar(self.paths.root / str(item.get("path", "")))
        metadata = read_json(sidecar, {}) if sidecar.exists() else {}
        if isinstance(metadata, dict):
            item.update({key: value for key, value in metadata.items() if key not in {"path", "type", "title"}})
        return item
    def update_asset_status(
        self,
        path: str,
        *,
        status: str,
        replaced_by: str | None = None,
        reason: str | None = None,
        source: str = "manual_cli",
    ) -> dict[str, Any]:
        target = self._resolve_proposal_target(path.removeprefix(f"{PRAXILE_DIR}/"))
        metadata: dict[str, Any] = {
            "status": status,
            "updated_at": utc_now(),
            "source": source,
        }
        if status == "active":
            metadata["reactivated_at"] = utc_now()
        if replaced_by:
            metadata["replaced_by"] = replaced_by
        if reason:
            key = {
                "active": "reactivated_reason",
                "deprecated": "deprecated_reason",
                "superseded": "superseded_reason",
                "archived": "archived_reason",
            }.get(status, "reason")
            metadata[key] = reason
        metadata = self._normalized_lifecycle_metadata(metadata)
        sidecar = self._asset_metadata_sidecar(target)
        current = read_json(sidecar, {}) if sidecar.exists() else {}
        if not isinstance(current, dict):
            current = {}
        if status == "active":
            for key in [
                "replaced_by",
                "deprecated_reason",
                "deprecated_at",
                "superseded_reason",
                "superseded_at",
                "archived_reason",
                "archived_at",
            ]:
                current.pop(key, None)
        event = _lifecycle_event_from_metadata(metadata, source=source)
        if event:
            current_events = current.get("lifecycle_events") if isinstance(current.get("lifecycle_events"), list) else []
            current["lifecycle_events"] = [*current_events, event][-50:]
        current.update(metadata)
        write_json(sidecar, current)
        self.index_asset(target)
        return self.get_asset(str(target.relative_to(self.paths.root))) or {"path": str(target.relative_to(self.paths.root)), **current}
    def model_routing_stats(self, *, limit: int = 200) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.list_history(limit=limit):
            trajectory_path = self.paths.root / row.get("trajectory_path", "")
            trajectory = read_json(trajectory_path, {}) if trajectory_path.exists() else {}
            if not trajectory:
                continue
            task_type = trajectory.get("task_analysis", {}).get("task_type") or "unknown"
            selected = trajectory.get("model_routing", {}).get("selected") or {}
            target = selected.get("target") or "(none)"
            key = (task_type, target)
            group = groups.setdefault(
                key,
                {
                    "task_type": task_type,
                    "target": target,
                    "route_key": selected.get("route_key"),
                    "runs": 0,
                    "completed": 0,
                    "needs_human": 0,
                    "failed": 0,
                    "reward_total": 0.0,
                    "reward_count": 0,
                    "model_calls": 0,
                    "latency_ms_total": 0,
                    "performance_failures": 0,
                    "failure_patterns": {},
                },
            )
            group["runs"] += 1
            status = trajectory.get("result", {}).get("status", "unknown")
            if status in {"completed", "needs_human", "failed"}:
                group[status] += 1
            reward = trajectory.get("reward_report", {}).get("overall")
            if isinstance(reward, (int, float)):
                group["reward_total"] += float(reward)
                group["reward_count"] += 1
            calls = trajectory.get("model_routing", {}).get("calls") or []
            group["model_calls"] += len(calls)
            for call in calls:
                latency = call.get("latency_ms")
                if isinstance(latency, (int, float)):
                    group["latency_ms_total"] += int(latency)
            for event in trajectory.get("model_routing", {}).get("performance") or []:
                if event.get("status") in {"unavailable", "invalid_action", "error"}:
                    group["performance_failures"] += 1
                    pattern = event.get("failure_pattern") or event.get("status")
                    group["failure_patterns"][pattern] = group["failure_patterns"].get(pattern, 0) + 1

        stats: list[dict[str, Any]] = []
        for group in groups.values():
            runs = max(1, int(group["runs"]))
            reward_count = int(group["reward_count"])
            model_calls = int(group["model_calls"])
            stats.append(
                {
                    **group,
                    "completion_rate": round(group["completed"] / runs, 4),
                    "needs_human_rate": round(group["needs_human"] / runs, 4),
                    "failure_rate": round(group["failed"] / runs, 4),
                    "average_reward": round(group["reward_total"] / reward_count, 4) if reward_count else None,
                    "average_latency_ms": round(group["latency_ms_total"] / model_calls, 2) if model_calls else None,
                }
            )
        stats.sort(key=lambda item: (-item["runs"], item["task_type"], item["target"]))
        return stats
    def cleanup_empty_dirs(self, root: Path) -> None:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
    def remove_all(self) -> None:
        if self.paths.state.exists():
            shutil.rmtree(self.paths.state)

    def _asset_metadata_sidecar(self, asset_target: Path) -> Path:
        return asset_target.with_name(f"{asset_target.name}.meta.json")

    def _normalized_lifecycle_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise ValueError("metadata_update requires a metadata object")
        result = dict(metadata)
        status = str(result.get("status", "active"))
        if status not in ASSET_LIFECYCLE_STATUSES:
            raise ValueError(f"Unsupported asset status: {status}")
        result["status"] = status
        return result



class AssetsRepository(StoreRepository):
    def list(self, kind: str, *, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self.store.list_assets(kind, include_inactive=include_inactive)

    def get(self, path: str) -> dict[str, Any] | None:
        return self.store.get_asset(path)

    def update_status(self, path: str, **kwargs: Any) -> dict[str, Any]:
        return self.store.update_asset_status(path, **kwargs)

    def usage(self, path: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.attribution_history_for_asset(path, limit=limit)
