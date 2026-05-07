import unittest
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch
from praxile.cli import main
import pytest

pytestmark = pytest.mark.integration

class TestIntegrationDemo(unittest.TestCase):
    @patch("sys.argv", ["praxile", "demo", "--fast", "--accept-first"])
    def test_demo_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Patch the demo path to use our tmp dir
            with patch("praxile.cli.cmd_demo") as mock_demo:
                # We can't easily mock cmd_demo if we want to run the real demo,
                # let's just patch sys.argv and run main, but pass --path
                pass
            
            # Actually let's just run it
            test_args = ["demo", "--fast", "--accept-first", "--path", str(root)]
            sys_args = ["praxile"] + test_args
            with patch.object(sys, "argv", sys_args):
                exit_code = main(test_args)
                self.assertEqual(exit_code, 0)
                
            # Verify it created .praxile
            self.assertTrue((root / ".praxile").exists())
            self.assertTrue((root / ".praxile" / "config.json").exists())

if __name__ == "__main__":
    unittest.main()
