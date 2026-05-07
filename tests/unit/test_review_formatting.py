import unittest
from praxile.cli import proposal_plain_language, proposal_review_guidance

class TestCLIReviewFormatting(unittest.TestCase):
    def test_proposal_plain_language(self):
        memory_prop = {"type": "memory_update", "action": "append", "target_path": "memory/project.md"}
        self.assertIn("remember a project-local lesson", proposal_plain_language(memory_prop))

        skill_prop = {"type": "skill_create", "title": "Foo"}
        self.assertIn("turn this run into a reusable step-by-step project skill", proposal_plain_language(skill_prop))

        arch_gate = {"type": "architecture_gate", "target_path": "rules/architecture-gates/db.md"}
        self.assertIn("pause for human review", proposal_plain_language(arch_gate))

    def test_proposal_review_guidance(self):
        low_risk = {"type": "memory_update", "risk_level": "low", "confidence_level": "high"}
        guidance = proposal_review_guidance(None, low_risk)
        self.assertEqual(guidance["action"], "accept")

        high_risk = {"type": "frozen_boundary"}
        guidance_hr = proposal_review_guidance(None, high_risk)
        self.assertEqual(guidance_hr["action"], "inspect")

        low_conf = {"type": "skill_create", "confidence_level": "low"}
        guidance_lc = proposal_review_guidance(None, low_conf)
        self.assertEqual(guidance_lc["action"], "reject_or_edit")

if __name__ == "__main__":
    unittest.main()
