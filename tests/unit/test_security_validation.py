import tempfile
import unittest
import json
from pathlib import Path
from praxile.config import Config
from praxile.security import SafetyPolicy
from praxile.tools import ToolRegistry

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

    def test_project_policy_file_blocks_tool_call(self):
        rules_dir = self.config.paths.state / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "safety-policy.json").write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "deny-policy-test-command",
                            "tool": "run_command",
                            "risk_level": "high",
                            "message": "blocked by test policy",
                            "match": {"command_contains": ["--blocked-by-policy"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        safety = SafetyPolicy(self.config)
        decision = safety.check_tool_call("run_command", {"command": "python -m pytest --blocked-by-policy"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "blocked by test policy")
        self.assertEqual(safety.policy_status()["rules_count"], 1)

    def test_missing_project_policy_file_does_not_block_allowed_command(self):
        safety = SafetyPolicy(self.config)
        decision = safety.check_tool_call("run_command", {"command": "python -m pytest"})
        self.assertTrue(decision.allowed)
        self.assertEqual(safety.policy_status()["policy_files"][0]["error"], "missing")

    def test_project_policy_file_blocks_write_path(self):
        rules_dir = self.config.paths.state / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "safety-policy.json").write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "deny-generated-src-edits",
                            "tool": "edit_file",
                            "message": "generated source must not be edited directly",
                            "match": {"path_glob": ["src/generated/**"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        decision = SafetyPolicy(self.config).check_tool_call("edit_file", {"path": "src/generated/client.py"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "generated source must not be edited directly")

    def test_policy_files_must_stay_inside_praxile_state(self):
        self.config.data["safety"]["policy_files"] = ["../unsafe-policy.json"]
        safety = SafetyPolicy(self.config)
        status = safety.policy_status()
        self.assertEqual(status["rules_count"], 0)
        self.assertIn("policy files must resolve inside .praxile/", status["errors"][0])

    def test_tool_registry_applies_project_policy_before_execution(self):
        self.config.data["safety"]["policy_rules"] = [
            {
                "id": "deny-echo-blocked",
                "tool": "run_command",
                "message": "runtime tool call blocked by policy",
                "match": {"command_contains": ["echo blocked"]},
            }
        ]
        registry = ToolRegistry(self.config)
        try:
            result = registry.execute({"type": "run_command", "command": "echo blocked"}, task_id="task_test", step=1)
        finally:
            registry.close()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["output"], "runtime tool call blocked by policy")

if __name__ == "__main__":
    unittest.main()
