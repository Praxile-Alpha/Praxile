import tempfile
import unittest
from pathlib import Path
from praxile.config import Config
from praxile.security import SafetyPolicy

class TestSecurityValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Use default config
        self.config = Config.load(self.root)
        self.safety = SafetyPolicy(self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_escape(self):
        decision = self.safety.check_path("../outside.txt", write=False)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "high")

    def test_sensitive_glob(self):
        decision = self.safety.check_path(".env", write=False)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "high")

    def test_protected_harness_path(self):
        decision = self.safety.check_path(".praxile/config.json", write=True)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "medium")

    def test_dangerous_command(self):
        decision = self.safety.check_command("rm -rf /")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "high")

    def test_command_substitution(self):
        decision = self.safety.check_command("echo $(ls)")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "high")

    def test_shell_features_blocked_by_default(self):
        decision = self.safety.check_command("ls && echo ok")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk_level, "medium")

if __name__ == "__main__":
    unittest.main()
