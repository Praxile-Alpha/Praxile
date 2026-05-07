from __future__ import annotations
import unittest
import tempfile
from praxile.config import Config
from praxile.reward import RewardEngine

class TestRewardEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from pathlib import Path
        self.root = Path(self.tmp.name)
        self.config = Config.load(self.root)
        self.engine = RewardEngine(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_report_success_no_edits(self):
        trajectory = {
            "result": {"status": "completed"},
            "cost": {"tool_calls": 2, "model_calls": 2},
        }
        test_results = []
        report = self.engine.build_report(trajectory, test_results)
        self.assertIn("task_success", report)
        self.assertIn("process_safety", report)

    def test_build_report_with_edits_and_tests_passed(self):
        trajectory = {
            "result": {"status": "completed"},
            "actions": [{"action_type": "edit_file", "status": "success"}],
            "cost": {"tool_calls": 5, "model_calls": 5},
        }
        test_results = [{"status": "success"}]
        report = self.engine.build_report(trajectory, test_results)
        self.assertEqual(report["regression_status"], "passed")
        self.assertEqual(report["regression_score"], 1.0)

    def test_build_report_blocked_actions(self):
        trajectory = {
            "result": {"status": "needs_human"},
            "actions": [{"status": "blocked", "risk_level": "high"}],
        }
        report = self.engine.build_report(trajectory, [])
        self.assertIn("process_safety", report)

    def test_build_report_high_cost(self):
        trajectory = {
            "result": {"status": "completed"},
            "cost": {"tool_calls": 50, "model_calls": 50},
        }
        report = self.engine.build_report(trajectory, [])
        self.assertIn("cost_score", report)

    def test_should_generate_experience(self):
        trajectory = {
            "actions": [{"action_type": "edit_file", "status": "success"}]
        }
        report = self.engine.build_report(trajectory, [])
        self.assertTrue(report["experience_generation"]["should_generate_experience"])

    def test_evidence_strength(self):
        trajectory = {
            "actions": [
                {"action_type": "edit_file", "status": "success"},
                {"action_type": "run_command", "status": "failure"}
            ]
        }
        test_results = [{"status": "success"}]
        report = self.engine.build_report(trajectory, test_results)
        self.assertEqual(report["experience_generation"]["evidence_strength"], "high")

if __name__ == "__main__":
    unittest.main()
