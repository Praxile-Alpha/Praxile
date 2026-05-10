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

    def test_spec_compliance_gap_lowers_reward_and_records_objective_signal(self):
        trajectory = {
            "result": {"status": "completed"},
            "actions": [{"action_type": "edit_file", "status": "success", "input": {"path": "search.py"}}],
            "spec_compliance": {
                "status": "partial",
                "score": 0.42,
                "missing": [{"text": "Add P95 verification"}],
                "violations": [{"type": "constraint", "text": "Use PostgreSQL only"}],
                "success_metric_coverage": [{"metric": "P95 < 200ms", "covered": False}],
            },
        }
        report = self.engine.build_report(trajectory, [{"status": "success"}])

        self.assertLess(report["task_success"], 0.8)
        self.assertLess(report["scope_control_score"], 0.75)
        self.assertEqual(report["objective_signals"]["spec_compliance_status"], "partial")
        self.assertEqual(report["objective_signals"]["spec_compliance_violation_count"], 1)
        self.assertTrue(any("Spec compliance needs review" in note for note in report["notes"]))

    def test_executor_attribution_is_reported(self):
        trajectory = {
            "task_id": "task_exec",
            "result": {"status": "completed"},
            "parallel_readonly_exploration": {"enabled": True, "action_count": 2},
            "executors": [
                {"executor_id": "coding_agent", "kind": "agent_runtime", "role": "coding"},
                {"executor_id": "parallel_readonly", "kind": "parallel_readonly_coordinator", "role": "pre_model_exploration"},
            ],
            "actions": [
                {
                    "action_type": "parallel_readonly_exploration",
                    "status": "success",
                    "executor": {"executor_id": "parallel_readonly", "kind": "parallel_readonly_coordinator"},
                    "observation": {
                        "data": {
                            "count": 2,
                            "executor_events": [
                                {"executor_id": "readonly_explorer_1", "kind": "readonly_worker", "role": "list_files"},
                                {"executor_id": "readonly_explorer_2", "kind": "readonly_worker", "role": "search"},
                            ],
                            "observations": [{"status": "success"}, {"status": "failure"}],
                        }
                    },
                },
                {
                    "action_type": "finish",
                    "status": "success",
                    "executor": {"executor_id": "coding_agent", "kind": "agent_runtime"},
                },
            ],
        }

        report = self.engine.build_report(trajectory, [])

        attribution = report["objective_signals"]["executor_attribution"]
        self.assertEqual(attribution["quality"], "complete")
        self.assertEqual(attribution["action_executor_counts"]["parallel_readonly"], 1)
        self.assertEqual(attribution["parallel_readonly"]["worker_count"], 2)
        self.assertEqual(attribution["parallel_readonly"]["failed_observation_count"], 1)
        self.assertTrue(report["experience_generation"]["signals"]["parallel_readonly_issue"])
        self.assertTrue(any("Parallel read-only exploration" in note for note in report["notes"]))

if __name__ == "__main__":
    unittest.main()
