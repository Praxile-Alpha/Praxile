from __future__ import annotations

from typing import Any

from .base import StoreRepository
from .common import *  # noqa: F401,F403


class TrajectoriesStoreMixin:
    def record_trajectory(self, trajectory: dict[str, Any]) -> Path:
        task_id = trajectory["task_id"]
        date = trajectory["start_time"][:10]
        path = self.paths.trajectories / f"{date}-{task_id}.json"
        trajectory["schema"] = PRAXILE_TRAJECTORY_SCHEMA
        trajectory["external_compat"] = {
            "format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
            "path": str((self.paths.trajectories / "external_compat.jsonl").relative_to(self.paths.root)),
            "source_of_truth": str(path.relative_to(self.paths.root)),
            "note": "Praxile structured JSON is the audit source of truth; JSONL sidecar is for research/compression import.",
        }
        write_json(path, trajectory)
        append_jsonl(self.paths.trajectories / "external_compat.jsonl", self._to_external_compat_entry(trajectory, path))
        metadata = self._asset_metadata(path)
        vector_record = self._asset_vector_record(metadata) if metadata else None
        with self._connection() as conn:
            self._index_trajectory_row(conn, trajectory, path)
            if metadata:
                self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                self._record_index_event_conn(conn, path, "trajectory_recorded", processed=True)
        return path
    def _index_trajectory_row(self, conn: sqlite3.Connection, trajectory: dict[str, Any], path: Path) -> None:
        report = trajectory.get("reward_report") or {}
        conn.execute(
            """
            INSERT OR REPLACE INTO tasks
            (task_id, user_task, status, reward_score, trajectory_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trajectory.get("task_id"),
                trajectory.get("user_task", ""),
                trajectory.get("result", {}).get("status", "unknown"),
                report.get("overall"),
                str(path.relative_to(self.paths.root)),
                trajectory.get("start_time", utc_now()),
                trajectory.get("end_time", trajectory.get("start_time", utc_now())),
            ),
        )
    def _to_external_compat_entry(self, trajectory: dict[str, Any], path: Path) -> dict[str, Any]:
        plan = "\n".join(f"- {item}" for item in trajectory.get("plan", []))
        actions = "\n".join(
            f"- #{item.get('step')} {item.get('action_type')} -> {item.get('status')}"
            for item in trajectory.get("actions", [])
        )
        report = trajectory.get("reward_report", {})
        route = trajectory.get("model_routing", {}).get("selected") or {}
        assistant_summary = (
            f"Plan:\n{plan or '(none)'}\n\n"
            f"Actions:\n{actions or '(none)'}\n\n"
            f"Result: {trajectory.get('result', {}).get('status')} — {trajectory.get('result', {}).get('summary')}\n"
            f"Reward overall: {report.get('overall')}"
        )
        return {
            "format": EXTERNAL_COMPAT_TRAJECTORY_FORMAT,
            "source": "praxile",
            "source_task_id": trajectory.get("task_id"),
            "source_path": str(path.relative_to(self.paths.root)),
            "timestamp": trajectory.get("end_time") or trajectory.get("start_time"),
            "model": route.get("target"),
            "completed": trajectory.get("result", {}).get("status") == "completed",
            "reward": report.get("overall"),
            "conversations": [
                {"from": "human", "value": trajectory.get("user_task", "")},
                {"from": "gpt", "value": assistant_summary},
            ],
        }
    def latest_trajectory(self) -> dict[str, Any] | None:
        files = sorted(self.paths.trajectories.glob("*.json"))
        if not files:
            return None
        return read_json(files[-1], {})
    def get_trajectory(self, task_id: str) -> dict[str, Any] | None:
        path = self.find_trajectory_path(task_id)
        return read_json(path, {}) if path else None
    def find_trajectory_path(self, task_id: str) -> Path | None:
        for path in self.paths.trajectories.glob(f"*-{task_id}.json"):
            return path
        for path in self.paths.trajectories.glob("*.json"):
            data = read_json(path, {})
            if data.get("task_id", "").startswith(task_id):
                return path
        return None
    def update_trajectory(self, trajectory: dict[str, Any]) -> Path:
        task_id = str(trajectory.get("task_id") or "")
        path = self.find_trajectory_path(task_id)
        if path is None:
            date = str(trajectory.get("start_time") or utc_now())[:10]
            path = self.paths.trajectories / f"{date}-{task_id}.json"
        write_json(path, trajectory)
        metadata = self._asset_metadata(path)
        vector_record = self._asset_vector_record(metadata) if metadata else None
        with self._connection() as conn:
            self._index_trajectory_row(conn, trajectory, path)
            if metadata:
                self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                self._record_index_event_conn(conn, path, "trajectory_updated", processed=True)
        return path
    def checkpoint_path(self, task_id: str) -> Path:
        return self.paths.checkpoints / f"{task_id}.json"
    def write_checkpoint(self, checkpoint: dict[str, Any]) -> Path:
        task_id = checkpoint["task_id"]
        path = self.checkpoint_path(task_id)
        checkpoint["updated_at"] = utc_now()
        write_json(path, checkpoint)
        return path
    def load_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        candidates = sorted(self.paths.checkpoints.glob(f"{task_id}*.json"))
        if not candidates:
            return None
        if len(candidates) == 1:
            return read_json(candidates[0], {})
        exact = self.checkpoint_path(task_id)
        if exact.exists():
            return read_json(exact, {})
        return read_json(candidates[-1], {})
    def delete_checkpoint(self, task_id: str) -> None:
        for path in self.paths.checkpoints.glob(f"{task_id}*.json"):
            try:
                path.unlink()
            except OSError:
                pass



class TrajectoriesRepository(StoreRepository):
    def latest(self) -> dict[str, Any] | None:
        return self.store.latest_trajectory()

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.store.get_trajectory(task_id)

    def record(self, trajectory: dict[str, Any]) -> None:
        self.store.record_trajectory(trajectory)
