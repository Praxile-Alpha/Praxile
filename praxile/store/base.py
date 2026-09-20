from __future__ import annotations

from typing import Any

from .common import *  # noqa: F401,F403


class StoreRepository:
    """Small delegation helper used by service-facing repository wrappers."""

    def __init__(self, store: Any):
        self.store = store


class BaseStore:
    def __init__(self, paths: ProjectPaths, config: Config | None = None):
        self.paths = paths
        self.config = config
    def initialize(self, config: Config, *, force: bool = False) -> None:
        self.config = config
        seeded_assets: list[Path] = []
        directories = [
            self.paths.state,
            self.paths.state / "memory",
            self.paths.state / "skills",
            self.paths.state / "experience" / "chat" / "sessions",
            self.paths.state / "experience" / "trajectories",
            self.paths.feedback,
            self.paths.state / "experience" / "failures",
            self.paths.state / "experience" / "patterns",
            self.paths.state / "experience" / "reflect",
            self.paths.state / "experience" / "artifacts",
            self.paths.state / "experience" / "proposals" / "pending",
            self.paths.state / "experience" / "proposals" / "accepted",
            self.paths.state / "experience" / "proposals" / "rejected",
            self.paths.state / "evals" / "checklists",
            self.paths.state / "evals" / "regression-cases",
            self.paths.state / "rules" / "frozen-boundaries",
            self.paths.state / "rules" / "architecture-gates",
            self.paths.state / "rules" / "harness-rules",
            self.paths.state / "db",
            self.paths.state / "logs",
            self.paths.state / "backups",
            self.paths.state / "cache",
            self.paths.checkpoints,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        self._recover_interrupted_proposal_commits()

        if force or not self.paths.config.exists():
            config.write()

        for name, description in MEMORY_FILES.items():
            path = self.paths.state / "memory" / f"{name}.md"
            if force or not path.exists():
                path.write_text(
                    f"# {name.title()} Memory\n\n"
                    f"{description}\n\n"
                    "<!-- Accepted memory updates are appended below. -->\n",
                    encoding="utf-8",
                )
                seeded_assets.append(path)

        constitution_path = self.paths.state / "constitution.md"
        if force or not constitution_path.exists():
            self._write_template("constitution.md", constitution_path)
            seeded_assets.append(constitution_path)

        gate_path = self.paths.state / "rules" / "architecture-gates" / "default.md"
        if force or not gate_path.exists():
            self._write_template("rules/architecture-gates/default.md", gate_path)
            seeded_assets.append(gate_path)

        harness_rule_path = self.paths.state / "rules" / "harness-rules" / "default.md"
        if force or not harness_rule_path.exists():
            self._write_template("rules/harness-rules/default.md", harness_rule_path)
            seeded_assets.append(harness_rule_path)

        safety_policy_path = self.paths.state / "rules" / "safety-policy.json"
        if force or not safety_policy_path.exists():
            self._write_template("rules/safety-policy.json", safety_policy_path)
            seeded_assets.append(safety_policy_path)

        self._init_db()
        if force:
            self.reindex_all()
        else:
            for path in seeded_assets:
                self.index_asset(path)
    def _write_template(self, template_path: str, target: Path) -> None:
        source = TEMPLATE_ROOT / template_path
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    def _connect(self) -> sqlite3.Connection:
        self.paths.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.paths.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  user_task TEXT NOT NULL,
                  status TEXT NOT NULL,
                  reward_score REAL,
                  trajectory_path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                  proposal_id TEXT PRIMARY KEY,
                  source_task_id TEXT,
                  type TEXT NOT NULL,
                  title TEXT NOT NULL,
                  status TEXT NOT NULL,
                  risk_level TEXT NOT NULL,
                  target_files TEXT NOT NULL,
                  path TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reward_profiles (
                  task_id TEXT PRIMARY KEY,
                  profile_id TEXT NOT NULL,
                  profile_version TEXT NOT NULL,
                  profile_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reward_claims (
                  claim_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  claim_type TEXT NOT NULL,
                  value_json TEXT,
                  provenance TEXT NOT NULL,
                  status TEXT NOT NULL,
                  profile_version TEXT NOT NULL,
                  evidence_refs TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reward_evidence (
                  evidence_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  evidence_type TEXT NOT NULL,
                  source_ref TEXT,
                  provenance TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS judge_calibration_runs (
                  calibration_id TEXT PRIMARY KEY,
                  judge TEXT NOT NULL,
                  report_path TEXT NOT NULL,
                  precision REAL,
                  recall REAL NOT NULL,
                  calibration_error REAL,
                  false_promotion_rate REAL,
                  disagreement_rate REAL NOT NULL,
                  abstention_rate REAL NOT NULL,
                  evidence_coverage REAL NOT NULL,
                  report_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS judge_observations (
                  task_id TEXT PRIMARY KEY,
                  self_judgment_score REAL,
                  verifier_score REAL,
                  verifier_available INTEGER NOT NULL DEFAULT 0,
                  promotion_eligible INTEGER NOT NULL DEFAULT 0,
                  calibration_error REAL,
                  false_promotion INTEGER NOT NULL DEFAULT 0,
                  observation_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reward_claims_task ON reward_claims(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reward_evidence_task ON reward_evidence(task_id)")
            self._ensure_columns(
                conn,
                "judge_calibration_runs",
                {
                    "precision": "REAL",
                    "calibration_error": "REAL",
                    "false_promotion_rate": "REAL",
                },
            )
            self._ensure_asset_schema(conn)
            self._ensure_graph_schema(conn)
    def _ensure_graph_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_nodes (
              node_id TEXT PRIMARY KEY,
              node_type TEXT NOT NULL,
              ref_path TEXT,
              title TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_edges (
              edge_id TEXT PRIMARY KEY,
              source_node_id TEXT NOT NULL,
              target_node_id TEXT NOT NULL,
              relation_type TEXT NOT NULL,
              confidence REAL,
              evidence TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_source ON experience_edges(source_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_target ON experience_edges(target_node_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_experience_edges_relation ON experience_edges(relation_type)")
    def _ensure_asset_schema(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'").fetchall()
        recreate = False
        if rows:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)").fetchall()}
            required = {
                "path",
                "type",
                "title",
                "content_hash",
                "summary",
                "tags",
                "source_task_id",
                "confidence",
                "mtime_ns",
                "size",
                "last_indexed_at",
                "usage_count",
                "positive_outcome_count",
                "negative_outcome_count",
                "last_used_at",
            }
            recreate = not required.issubset(columns)
        if recreate:
            conn.execute("DROP TABLE IF EXISTS assets")
            conn.execute("DROP TABLE IF EXISTS assets_fts")
            conn.execute("DROP TABLE IF EXISTS asset_vectors")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
              path TEXT PRIMARY KEY,
              type TEXT NOT NULL,
              title TEXT,
              content_hash TEXT,
              summary TEXT,
              tags TEXT,
              source_task_id TEXT,
              confidence REAL,
              mtime_ns INTEGER,
              size INTEGER,
              status TEXT NOT NULL,
              usage_count INTEGER NOT NULL DEFAULT 0,
              positive_outcome_count INTEGER NOT NULL DEFAULT 0,
              negative_outcome_count INTEGER NOT NULL DEFAULT 0,
              last_used_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_indexed_at TEXT
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
                  path UNINDEXED,
                  title,
                  content,
                  tags,
                  type UNINDEXED
                )
                """
            )
        except sqlite3.OperationalError:
            conn.execute("DROP TABLE IF EXISTS assets_fts")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_vectors (
              path TEXT PRIMARY KEY,
              content_hash TEXT NOT NULL,
              provider TEXT NOT NULL,
              model TEXT,
              dims INTEGER NOT NULL,
              vector_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_index_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
              event TEXT NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              processed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL,
              task_id TEXT NOT NULL,
              matched_terms TEXT,
              matched_fields TEXT,
              why_loaded TEXT,
              score REAL,
              used_in_prompt INTEGER NOT NULL DEFAULT 1,
              referenced INTEGER NOT NULL DEFAULT 0,
              used_explicitly INTEGER NOT NULL DEFAULT 0,
              semantic_attribution TEXT,
              outcome TEXT NOT NULL DEFAULT 'unknown',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        self._ensure_columns(
            conn,
            "asset_usage",
            {
                "referenced": "INTEGER NOT NULL DEFAULT 0",
                "used_explicitly": "INTEGER NOT NULL DEFAULT 0",
                "semantic_attribution": "TEXT",
            },
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_activation_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              task_id TEXT NOT NULL,
              path TEXT NOT NULL,
              asset_version TEXT,
              stage TEXT NOT NULL,
              outcome TEXT NOT NULL DEFAULT 'unknown',
              contribution TEXT NOT NULL DEFAULT 'unknown',
              model_role TEXT,
              executor_id TEXT,
              evidence TEXT,
              metadata TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asset_activation_task ON asset_activation_events(task_id, stage)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asset_activation_path ON asset_activation_events(path, created_at)"
        )
        self._migrate_legacy_asset_usage_activation(conn)

    def _migrate_legacy_asset_usage_activation(self, conn: sqlite3.Connection) -> None:
        stages = [
            ("eligible", "1 = 1"),
            ("retrieved", "1 = 1"),
            ("injected", "usage.used_in_prompt = 1"),
            ("referenced", "usage.referenced = 1"),
        ]
        for stage, condition in stages:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO asset_activation_events
                (event_id, task_id, path, asset_version, stage, outcome, contribution,
                 evidence, metadata, created_at)
                SELECT
                  'legacy:' || usage.id || ':{stage}',
                  usage.task_id,
                  usage.path,
                  assets.content_hash,
                  '{stage}',
                  CASE WHEN '{stage}' = 'referenced' THEN usage.outcome ELSE 'unknown' END,
                  'unknown',
                  '{{"source":"legacy_asset_usage","causal_credit":false}}',
                  '{{"migration":"asset_usage_v1"}}',
                  usage.created_at
                FROM asset_usage AS usage
                LEFT JOIN assets ON assets.path = usage.path
                WHERE {condition}
                  AND NOT EXISTS (
                    SELECT 1 FROM asset_activation_events AS event
                    WHERE event.task_id = usage.task_id
                      AND event.path = usage.path
                      AND event.stage = '{stage}'
                  )
                """
            )
    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
