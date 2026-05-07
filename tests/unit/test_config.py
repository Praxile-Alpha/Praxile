import tempfile
import unittest
from pathlib import Path

from praxile.config import Config, ConfigValidationError, default_config

class TestConfig(unittest.TestCase):
    def test_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = default_config(root)
            self.assertEqual(cfg["project"]["name"], root.name)
            self.assertEqual(cfg["runtime"]["max_steps"], 10)
            self.assertTrue(cfg["context"]["compression_enabled"])
            self.assertEqual(cfg["model_providers"], {})
            self.assertEqual(cfg["model_roles"], {"embedding": {"provider": "local", "model": "local_hash"}})
            self.assertNotIn("default_model", cfg["routing"])

    def test_config_load_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_file = root / ".praxile" / "config.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text('{"runtime": {"max_steps": 50}}', encoding="utf-8")
            
            cfg = Config.load(root)
            self.assertEqual(cfg.get("runtime", "max_steps"), 50)
            # Default fallback
            self.assertEqual(cfg.get("runtime", "model_timeout_seconds"), 30)

    def test_invalid_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_file = root / ".praxile" / "config.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_file.write_text('{"runtime": {"max_steps": 50', encoding="utf-8") # Invalid JSON
            
            with self.assertRaises(ValueError):
                Config.load(root)

if __name__ == "__main__":
    unittest.main()
