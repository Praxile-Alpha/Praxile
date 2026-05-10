import unittest
from unittest.mock import MagicMock
from praxile.cli import build_run_explanation, _asset_attribution_level

class TestExplain(unittest.TestCase):
    def test_build_run_explanation(self):
        store = MagicMock()
        store.usage_for_task.return_value = []
        store.get_asset.return_value = {
            "type": "memory",
            "title": "Project Context",
            "status": "archived",
            "archived_reason": "No longer needed",
            "usage_count": 5,
            "positive_outcome_count": 4,
            "negative_outcome_count": 1
        }
        store.find_proposal.return_value = {
            "proposal_id": "prop_1",
            "type": "skill_create",
            "title": "New skill",
            "status": "pending",
            "risk_level": "low"
        }
        
        trajectory = {
            "task_id": "test_1",
            "user_task": "Do something",
            "result": {"status": "completed"},
            "loaded_assets": [
                {
                    "path": "memory/project.md",
                    "score": 0.9,
                    "why_loaded": "Matches project",
                    "usage_count": 5,
                    "positive_outcome_count": 4,
                    "negative_outcome_count": 1,
                    "status": "archived",
                    "archived_reason": "No longer needed"
                }
            ],
            "experience_candidates": [
                {
                    "proposal_id": "prop_1",
                    "type": "skill_create",
                    "title": "New skill",
                    "status": "pending",
                    "risk_level": "low"
                }
            ]
        }
        
        explanation = build_run_explanation(store, trajectory)
        self.assertEqual(explanation["task_id"], "test_1")
        self.assertEqual(len(explanation["used"]), 1)
        self.assertEqual(explanation["used"][0]["status"], "archived")
        self.assertEqual(explanation["used"][0]["archived_reason"], "No longer needed")
        self.assertEqual(explanation["used"][0]["why_loaded"], "Matches project")
        self.assertEqual(explanation["used"][0]["attribution_level"], "mixed")
        self.assertEqual(len(explanation["produced"]), 1)
        self.assertEqual(explanation["produced"][0]["proposal_id"], "prop_1")

    def test_asset_attribution_level(self):
        self.assertEqual(_asset_attribution_level({"path": "memory/project.md"}), "loaded_only")
        self.assertEqual(_asset_attribution_level({"referenced": True}), "referenced")
        self.assertEqual(_asset_attribution_level({"used_explicitly": True}), "referenced")
        self.assertEqual(_asset_attribution_level({"used_explicitly": True, "positive_outcome_count": 1}), "strong_positive")
        self.assertEqual(_asset_attribution_level({"path": "x", "positive_outcome_count": 2}), "weak_positive")
        self.assertEqual(_asset_attribution_level({"path": "x", "negative_outcome_count": 1}), "weak_negative")
        self.assertEqual(
            _asset_attribution_level({"path": "x", "positive_outcome_count": 1, "negative_outcome_count": 1}),
            "mixed",
        )
        self.assertEqual(_asset_attribution_level({"path": "x", "user_helpful_count": 1}), "strong_positive")
        self.assertEqual(_asset_attribution_level({"path": "x", "user_harmful_count": 1}), "harmful")

if __name__ == "__main__":
    unittest.main()
