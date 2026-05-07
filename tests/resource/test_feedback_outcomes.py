import unittest
import tempfile
import json
from pathlib import Path
import pytest
from praxile.config import Config
from praxile.store import ExperienceStore
from praxile.cli import record_duplicate_warning_decision, _duplicate_warning_feedback

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]

class TestFeedbackOutcomes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = Config.load(self.root)
        self.store = ExperienceStore(self.config.paths)
        self.store.initialize(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_and_retrieve_duplicate_feedback(self):
        proposal = {"proposal_id": "test_prop_1", "type": "skill_create"}
        guidance = {"duplicate_warning": "warning_id=abc12345; similar_asset=foo; reason=bar"}
        
        record_duplicate_warning_decision(self.store, proposal, guidance, "accepted_anyway")
        
        # Check logs
        log_file = self.store.paths.logs / "duplicate_warnings.jsonl"
        self.assertTrue(log_file.exists())
        lines = log_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["warning_id"], "abc12345")
        self.assertEqual(event["user_action"], "accepted_anyway")

        # Check retrieval
        counts = _duplicate_warning_feedback(self.store, "abc12345")
        self.assertEqual(counts["ignored_or_accepted"], 1)
        self.assertEqual(counts["merged_or_rejected"], 0)

if __name__ == "__main__":
    unittest.main()
