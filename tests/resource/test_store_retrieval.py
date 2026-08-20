from __future__ import annotations
import tempfile
from pathlib import Path
import pytest
from praxile.config import Config
from praxile.runtime import AgentRuntime
from praxile.store import ExperienceStore
from praxile.evolution import EvolutionEngine

pytestmark = pytest.mark.resource

@pytest.mark.runtime_resource
@pytest.mark.sqlite_resource
def test_accepting_proposal_writes_auditable_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        trajectory = AgentRuntime(config).run("记录项目上下文", max_steps=1)
        proposal_id = trajectory["experience_candidates"][0]["proposal_id"]
        proposal = store.find_proposal(proposal_id, status="pending")
        assert proposal is not None
        assert proposal["source"]["task_id"] == trajectory["task_id"]
        assert "evidence" in proposal
        assert "confidence" in proposal
        assert "applicability_scope" in proposal
        accepted = store.apply_proposal(proposal)
        assert accepted["status"] == "accepted"
        project_memory = (root / ".praxile" / "memory" / "project.md").read_text(encoding="utf-8")
        assert trajectory["task_id"] in project_memory

@pytest.mark.sqlite_resource
def test_retrieval_priority_loads_rules_before_skills_and_memory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        skill_dir = root / ".praxile" / "skills" / "auth-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Auth Review\n\nauth session schema\n", encoding="utf-8")
        (root / ".praxile" / "memory" / "project.md").write_text("# Project\n\nauth session schema\n", encoding="utf-8")
        (root / ".praxile" / "rules" / "frozen-boundaries" / "auth.md").write_text(
            "# Auth Boundary\n\nauth session schema\n",
            encoding="utf-8",
        )
        store.reindex_all()
        results = store.retrieve("auth session schema", limit=20)
        kinds = [item["kind"] for item in results]
        assert kinds[0] == "rule"
        assert "skill" in kinds
        assert kinds.index("skill") < kinds.index("memory")
        assert all(item["loaded_by"] == "praxile" for item in results)

@pytest.mark.sqlite_resource
def test_accepting_project_pattern_indexes_pattern_asset() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        engine = EvolutionEngine(config)
        proposal = engine._proposal(
            source_task_id="mine_patterns",
            proposal_type="project_pattern",
            title="Record project pattern `parser-json`",
            reason="Mined from multiple source episodes.",
            risk_level="medium",
            evidence=["Evidence count: 3"],
            confidence=0.85,
            changes=[
                {
                    "path": "experience/patterns/parser-json.md",
                    "operation": "write",
                    "content": "# Parser JSON Pattern\n\n## Claim\nJSON parser repair\n",
                }
            ],
        )
        store.write_proposal(proposal)
        accepted = store.apply_proposal(proposal)
        assert accepted["status"] == "accepted"
        results = store.retrieve("JSON parser repair", kinds=["pattern"], limit=3)
        assert results
        assert results[0]["type"] == "project_pattern"

@pytest.mark.sqlite_resource
def test_loaded_assets_do_not_receive_positive_outcome_without_reference() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        memory_path = root / ".praxile" / "memory" / "project.md"
        memory_path.write_text("# Project\n\nParser memory\n", encoding="utf-8")
        store.index_asset(memory_path)
        store.record_asset_usage(
            "task_attr",
            [{"path": ".praxile/memory/project.md", "score": 0.9}],
            used_in_prompt=True,
        )
        store.update_asset_usage_outcome("task_attr", "success")
        asset = store.get_asset(".praxile/memory/project.md")
        assert asset is not None
        assert asset["positive_outcome_count"] == 0

        store.record_asset_usage(
            "task_attr_2",
            [{"path": ".praxile/memory/project.md", "score": 0.9}],
            used_in_prompt=True,
        )
        store.update_asset_usage_outcome(
            "task_attr_2",
            "success",
            referenced_paths=[".praxile/memory/project.md"],
        )
        asset = store.get_asset(".praxile/memory/project.md")
        assert asset is not None
        assert asset["positive_outcome_count"] == 0
        usage = store.usage_for_task("task_attr_2")
        assert usage[0]["referenced"] is True
        assert usage[0]["attribution_level"] == "referenced"
        funnel = store.activation_funnel_for_task("task_attr_2")
        assert funnel["stage_counts"] == {
            "eligible": 1,
            "retrieved": 1,
            "injected": 1,
            "referenced": 1,
            "complied_with": 0,
            "outcome_attributed": 0,
        }
        assert funnel["metrics"]["activation_rate"] == 1.0
        assert funnel["metrics"]["attribution_coverage"] == 0.0


@pytest.mark.sqlite_resource
def test_activation_funnel_requires_explicit_semantic_credit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        memory_path = root / ".praxile" / "memory" / "project.md"
        memory_path.write_text("# Project\n\nParser memory\n", encoding="utf-8")
        store.index_asset(memory_path)
        path = ".praxile/memory/project.md"
        store.record_asset_usage(
            "task_activation",
            [{"path": path, "score": 0.9, "model_role": "coding_agent", "executor_id": "coding_agent"}],
            used_in_prompt=True,
        )
        store.update_asset_usage_outcome(
            "task_activation",
            "success",
            attribution_results=[
                {
                    "path": path,
                    "referenced": True,
                    "used_explicitly": True,
                    "attribution_level": "strong_positive",
                    "confidence": 0.9,
                    "evidence": ["The run followed the parser strategy."],
                    "reason": "Matched edit and verification sequence.",
                    "should_update_asset_outcome": True,
                    "semantic_judge": {"active": True, "role": "attribution_judge"},
                }
            ],
        )

        funnel = store.activation_funnel_for_task("task_activation")
        assert funnel["stage_counts"]["complied_with"] == 1
        assert funnel["stage_counts"]["outcome_attributed"] == 1
        assert funnel["metrics"]["positive_contribution_rate"] == 1.0
        assert funnel["assets"][0]["contribution"] == "positive"
        assert funnel["assets"][0]["events"][-1]["model_role"] == "attribution_judge"
        assert funnel["assets"][0]["events"][-1]["executor_id"] == "coding_agent"
        assert store.get_asset(path)["positive_outcome_count"] == 1


@pytest.mark.sqlite_resource
def test_legacy_loaded_usage_migrates_without_compliance_or_credit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        store = ExperienceStore(config.paths)
        store.initialize(config)
        now = "2026-08-12T00:00:00+00:00"
        with store._connection() as conn:
            conn.execute("DELETE FROM asset_activation_events")
            conn.execute(
                """
                INSERT INTO asset_usage
                (path, task_id, matched_terms, matched_fields, why_loaded, score, used_in_prompt,
                 referenced, used_explicitly, outcome, created_at, updated_at)
                VALUES (?, ?, '[]', '[]', 'legacy load', 0.5, 1, 0, 0, 'success', ?, ?)
                """,
                (".praxile/memory/project.md", "legacy_task", now, now),
            )

        migrated_store = ExperienceStore(config.paths)
        funnel = migrated_store.activation_funnel_for_task("legacy_task")

        assert funnel["stage_counts"] == {
            "eligible": 1,
            "retrieved": 1,
            "injected": 1,
            "referenced": 0,
            "complied_with": 0,
            "outcome_attributed": 0,
        }
        assert funnel["assets"][0]["contribution"] == "unknown"
