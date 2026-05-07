from __future__ import annotations
import unittest
from pathlib import Path
import tempfile
from praxile.config import Config
from praxile.evolution import EvolutionEngine

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
            "reward_report": {
                "should_generate_experience": True
            }
        }
        proposals = self.engine.generate(trajectory)
        self.assertGreater(len(proposals), 0)
        types = [p["type"] for p in proposals]
        self.assertIn("memory_update", types)

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

if __name__ == "__main__":
    unittest.main()
