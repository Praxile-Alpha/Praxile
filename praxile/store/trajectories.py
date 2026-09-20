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
            self._index_reward_evidence_conn(conn, trajectory)
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
            self._index_reward_evidence_conn(conn, trajectory)
            if metadata:
                self._upsert_asset_metadata_conn(conn, metadata, vector_record)
                self._record_index_event_conn(conn, path, "trajectory_updated", processed=True)
        return path
    def _index_reward_evidence_conn(self, conn: sqlite3.Connection, trajectory: dict[str, Any]) -> None:
        task_id = str(trajectory.get("task_id") or "")
        report = trajectory.get("reward_report") if isinstance(trajectory.get("reward_report"), dict) else {}
        profile = report.get("reward_profile") if isinstance(report.get("reward_profile"), dict) else {}
        graph = report.get("evidence_graph") if isinstance(report.get("evidence_graph"), dict) else {}
        conn.execute("DELETE FROM reward_claims WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM reward_evidence WHERE task_id = ?", (task_id,))
        if profile:
            conn.execute(
                """
                INSERT OR REPLACE INTO reward_profiles(task_id, profile_id, profile_version, profile_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    profile.get("profile_id"),
                    profile.get("profile_version"),
                    json.dumps(profile, ensure_ascii=False),
                    trajectory.get("end_time") or utc_now(),
                ),
            )
        for item in graph.get("nodes") or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO reward_evidence
                (evidence_id, task_id, evidence_type, source_ref, provenance, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("evidence_id"), task_id, item.get("type"), item.get("source_ref"),
                    item.get("provenance"), json.dumps(item.get("payload"), ensure_ascii=False), utc_now(),
                ),
            )
        for item in graph.get("claims") or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO reward_claims
                (claim_id, task_id, claim_type, value_json, provenance, status, profile_version, evidence_refs, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("claim_id"), task_id, item.get("claim_type"),
                    json.dumps(item.get("value"), ensure_ascii=False), item.get("provenance"), item.get("status"),
                    item.get("profile_version"), json.dumps(item.get("evidence_refs") or []), utc_now(),
                ),
            )
    def reward_evidence_for_task(self, task_id: str) -> dict[str, Any]:
        trajectory = self.get_trajectory(task_id)
        if not trajectory:
            return {"task_id": task_id, "found": False}
        report = trajectory.get("reward_report") or {}
        return {
            "task_id": trajectory.get("task_id"),
            "found": True,
            "overall": report.get("overall"),
            "reward_profile": report.get("reward_profile") or {},
            "evidence_graph": report.get("evidence_graph") or {},
            "escalation": report.get("escalation") or {},
        }
    def record_judge_calibration(self, report: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO judge_calibration_runs
                (calibration_id, judge, report_path, precision, recall, calibration_error,
                 false_promotion_rate, disagreement_rate, abstention_rate, evidence_coverage,
                 report_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.get("calibration_id"), report.get("judge"), report.get("path"),
                    report.get("precision"), report.get("recall", 0.0), report.get("calibration_error"),
                    report.get("false_promotion_rate"), report.get("disagreement_rate", 0.0),
                    report.get("abstention_rate", 0.0), report.get("evidence_coverage", 0.0),
                    json.dumps(report, ensure_ascii=False), report.get("created_at") or utc_now(),
                ),
            )
    def list_judge_calibrations(self, judge: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = "SELECT report_json FROM judge_calibration_runs"
        params: list[Any] = []
        if judge:
            query += " WHERE judge = ?"
            params.append(judge)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [json.loads(row["report_json"]) for row in rows]
    def record_judge_observation(self, observation: dict[str, Any]) -> None:
        calibration = observation.get("judgment_calibration") or {}
        self_judgment = observation.get("self_judgment") or {}
        verifier = observation.get("verifier_outcome") or {}
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO judge_observations
                (task_id, self_judgment_score, verifier_score, verifier_available,
                 promotion_eligible, calibration_error, false_promotion,
                 observation_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.get("task_id"), self_judgment.get("score"), verifier.get("score"),
                    int(bool(verifier.get("available"))), int(bool(calibration.get("promotion_eligible"))),
                    calibration.get("absolute_error"), int(bool(calibration.get("false_promotion"))),
                    json.dumps(observation, ensure_ascii=False), observation.get("created_at") or utc_now(),
                    observation.get("updated_at") or utc_now(),
                ),
            )
    def get_judge_observation(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT observation_json FROM judge_observations WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return json.loads(row["observation_json"]) if row else None
    def list_judge_observations(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT observation_json FROM judge_observations ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [json.loads(row["observation_json"]) for row in rows]
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
