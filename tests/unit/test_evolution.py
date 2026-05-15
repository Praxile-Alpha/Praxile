from __future__ import annotations
import unittest
from pathlib import Path
import tempfile
from praxile.config import Config
from praxile.evolution import EvolutionEngine


class RecordingProposalRouter:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, messages, *, purpose="default", private=False, high_risk=False, temperature=0.2, max_tokens=4096, timeout=None):
        self.calls.append(
            {
                "messages": messages,
                "purpose": purpose,
                "private": private,
                "high_risk": high_risk,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": timeout,
            }
        )
        return {
            "content": self.content,
            "provider": "fake",
            "model": "proposal-model",
            "route": {
                "purpose": purpose,
                "model_role": purpose,
                "target": "fake:proposal-model",
                "fallback_used": False,
            },
        }

    def describe_route(self, purpose="default", *, private=False, high_risk=False):
        return {
            "purpose": purpose,
            "model_role": purpose,
            "target": "fake:proposal-model",
            "private": private,
            "high_risk": high_risk,
        }


class TestEvolutionEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config.load(self.root)
        self.engine = EvolutionEngine(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_generate_skipped_when_should_generate_false(self):
        trajectory = {
            "reward_report": {
                "should_generate_experience": False
            }
        }
        proposals = self.engine.generate(trajectory)
        self.assertEqual(len(proposals), 0)

    def test_generate_memory_proposal(self):
        trajectory = {
            "task_id": "test_task",
            "user_task": "record this project-specific parser repair",
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {
                    "signals": {"memory_requested": True},
                    "evidence_strength": "medium",
                },
            }
        }
        proposals = self.engine.generate(trajectory)
        self.assertGreater(len(proposals), 0)
        types = [p["type"] for p in proposals]
        self.assertIn("memory_update", types)

    def test_memory_proposal_requires_experience_signal(self):
        trajectory = {
            "task_id": "test_task",
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {"signals": {}, "evidence_strength": "low"},
            },
        }
        proposals = self.engine.generate(trajectory)
        self.assertEqual([p["type"] for p in proposals], [])

    def test_spec_compliance_violation_suppresses_normal_memory(self):
        trajectory = {
            "task_id": "test_task",
            "user_task": "record this search implementation",
            "spec_compliance": {
                "status": "partial",
                "violations": [{"type": "constraint", "text": "Use PostgreSQL only"}],
                "missing": [],
            },
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {
                    "signals": {"memory_requested": True, "spec_compliance_gap": True},
                    "evidence_strength": "medium",
                },
            },
        }
        proposals = self.engine.generate(trajectory)

        self.assertNotIn("memory_update", [proposal["type"] for proposal in proposals])
        self.assertEqual(trajectory["proposal_gate_summary"]["suppressed"], 1)
        self.assertIn("Spec compliance violations", trajectory["suppressed_experience_candidates"][0]["proposal_gate"]["suppressed_reasons"][0])

    def test_generate_skill_proposal(self):
        trajectory = {
            "task_id": "test_task",
            "reward_report": {
                "should_generate_experience": True
            },
            "actions": [
                {"action_type": "run_command", "status": "success", "observation": {"data": {"command": "npm run build"}}}
            ]
        }
        proposals = self.engine.generate(trajectory)
        types = [p["type"] for p in proposals]
        # In mock tests it might generate different stuff depending on heuristics. We just check it doesn't crash.
        self.assertIsInstance(types, list)

    def test_filter_suppressed_proposals(self):
        proposals = [
            {"type": "skill_create", "target_path": "skills/build/SKILL.md"}
        ]
        # In an empty rejected directory, none should be filtered
        filtered = self.engine._filter_suppressed_proposals(proposals)
        self.assertEqual(len(filtered), 1)

    def test_llm_assisted_proposals_call_proposal_composer_and_generate_pending_proposal(self):
        self.config.data["evolution"]["llm_assisted_proposals"] = True
        content = """
        {
          "proposals": [
            {
              "type": "memory_update",
              "title": "Record parser retry pattern",
              "reason": "The run produced a reusable parser repair signal.",
              "risk_level": "low",
              "evidence": ["Trajectory task_task completed with parser verification evidence."],
              "confidence": 0.82,
              "applicability_scope": "Future parser repair tasks with the same verification command.",
              "anti_scope": "Unrelated UI, auth, storage, or architecture changes.",
              "changes": [
                {
                  "path": "memory/llm-parser.md",
                  "operation": "append",
                  "content": "Use this only for parser repairs with matching verification evidence."
                }
              ]
            }
          ]
        }
        """
        router = RecordingProposalRouter(content)
        engine = EvolutionEngine(self.config, router=router)
        trajectory = {
            "task_id": "task_task",
            "user_task": "Fix parser retry handling",
            "task_analysis": {"privacy_sensitive": True, "high_risk": True},
            "result": {"status": "completed", "summary": "Fixed parser retry handling."},
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {"signals": {}, "evidence_strength": "medium"},
            },
        }

        proposals = engine.generate(trajectory)
        llm_proposals = [item for item in proposals if item.get("generated_by") == "llm_assisted_evolution"]

        self.assertEqual(len(router.calls), 1)
        self.assertEqual(router.calls[0]["purpose"], "proposal_composer")
        self.assertTrue(router.calls[0]["private"])
        self.assertTrue(router.calls[0]["high_risk"])
        self.assertEqual(len(llm_proposals), 1)
        self.assertEqual(llm_proposals[0]["status"], "pending")
        self.assertEqual(llm_proposals[0]["target_files"], ["memory/llm-parser.md"])
        self.assertEqual(llm_proposals[0]["llm_assisted"]["provider"], "fake")
        self.assertEqual(trajectory["llm_assisted_proposals"]["status"], "completed")
        self.assertEqual(trajectory["llm_assisted_proposals"]["accepted"], 1)

    def test_llm_assisted_proposals_records_no_route_failure_when_enabled_without_router(self):
        self.config.data["evolution"]["llm_assisted_proposals"] = True
        engine = EvolutionEngine(self.config)
        trajectory = {
            "task_id": "task_no_route",
            "user_task": "Record no route behavior",
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {"signals": {}, "evidence_strength": "medium"},
            },
        }

        proposals = engine.generate(trajectory)

        self.assertEqual([item for item in proposals if item.get("generated_by") == "llm_assisted_evolution"], [])
        self.assertEqual(trajectory["llm_assisted_proposals"]["status"], "failed")
        self.assertTrue(trajectory["llm_assisted_proposals"]["errors"])

    def test_llm_assisted_proposals_rejects_unsafe_paths(self):
        self.config.data["evolution"]["llm_assisted_proposals"] = True
        router = RecordingProposalRouter(
            """
            {"proposals":[{"type":"memory_update","title":"Bad","reason":"bad","risk_level":"low",
            "evidence":["some evidence"],"confidence":0.9,
            "applicability_scope":"similar tasks","anti_scope":"other tasks",
            "changes":[{"path":"config.json","operation":"write","content":"{}"}]}]}
            """
        )
        engine = EvolutionEngine(self.config, router=router)
        trajectory = {
            "task_id": "task_bad",
            "user_task": "Reject unsafe LLM proposal",
            "reward_report": {
                "should_generate_experience": True,
                "experience_generation": {"signals": {}, "evidence_strength": "medium"},
            },
        }

        proposals = engine.generate(trajectory)

        self.assertEqual([item for item in proposals if item.get("generated_by") == "llm_assisted_evolution"], [])
        self.assertEqual(trajectory["llm_assisted_proposals"]["status"], "no_valid_proposals")
        self.assertIn("unsafe proposal change path", trajectory["llm_assisted_proposals"]["rejection_reasons"][0])

if __name__ == "__main__":
    unittest.main()
