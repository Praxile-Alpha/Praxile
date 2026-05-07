from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import pytest

from praxile.config import Config
from praxile.adapter_bridge import OptionalAdapterBridge
from praxile.environment import FileSystemEnv, GitEnv, ProjectEnv, ShellEnv, TestEnv
from praxile.identity import agent_manifest
from praxile.interop import interop_policy
from praxile.security import SafetyPolicy
from praxile.store import ExperienceStore

pytestmark = [pytest.mark.resource, pytest.mark.sqlite_resource]


class PraxileHarnessTests(unittest.TestCase):
    def test_init_creates_local_evolution_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Config.load(root)
            store = ExperienceStore(config.paths)
            store.initialize(config)

            self.assertTrue((root / ".praxile" / "config.json").exists())
            self.assertTrue((root / ".praxile" / "memory" / "project.md").exists())
            self.assertTrue((root / ".praxile" / "rules" / "harness-rules" / "default.md").exists())
            self.assertIn(
                "Gate behavior",
                (root / ".praxile" / "rules" / "architecture-gates" / "default.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Privacy-sensitive tasks",
                (root / ".praxile" / "rules" / "harness-rules" / "default.md").read_text(encoding="utf-8"),
            )

            policy = interop_policy(config)
            self.assertFalse(policy["skills"]["external_framework_autoloads_praxile_skills"])
            self.assertTrue(policy["skills"]["praxile_loads_project_skills"])
            self.assertEqual(policy["memory"]["external_global_memory_write"], "never automatic")
            self.assertEqual(policy["agent"]["kind"], "standalone_self_evolving_agent_harness")
            self.assertEqual(policy["agent"]["state_root"], ".praxile/")
            self.assertIn("skills", policy["agent"]["adapter_matrix"])

    def test_agent_manifest_and_adapter_bridge_keep_praxile_boundary(self) -> None:
        manifest = agent_manifest()
        bridge = OptionalAdapterBridge()
        bridged = bridge.manifest()

        self.assertEqual(manifest["id"], "praxile.local-self-evolving-agent")
        self.assertEqual(manifest["scope"], "local_code_project_self_evolution")
        self.assertIn("requiring Hermes or OpenClaw to run", manifest["explicit_non_goals"])
        self.assertIn("automatic external global memory writes", manifest["explicit_non_goals"])
        self.assertIn("project_local_skills", manifest["owned_capabilities"])
        self.assertIn("agent_runtime", manifest["owned_capabilities"])
        self.assertEqual(bridged["adapter_bridge"]["mode"], "optional_read_only_detection")
        self.assertFalse(bridged["adapter_bridge"]["imports_adapter_modules"])
        self.assertIn("providers", bridged["adapter_bridge"]["capabilities"])
        self.assertIn("memory", bridged["adapter_matrix"])

    def test_file_edit_has_backup_and_task_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.txt").write_text("old\n", encoding="utf-8")
            config = Config.load(root)
            ExperienceStore(config.paths).initialize(config)
            safety = SafetyPolicy(config)
            fs = FileSystemEnv(config, safety)

            observation = fs.write_file("hello.txt", "new\n", task_id="task_test", step=1)
            shell = ShellEnv(config, safety)
            project = ProjectEnv(config, fs, GitEnv(config), TestEnv(config, shell))
            restored = project.rollback_task(
                {
                    "actions": [
                        {
                            "action_type": "edit_file",
                            "status": "success",
                            "observation": observation.to_dict(),
                        }
                    ]
                }
            )

            self.assertEqual((root / "hello.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(restored[0]["mode"], "restored_backup")


if __name__ == "__main__":
    unittest.main()
