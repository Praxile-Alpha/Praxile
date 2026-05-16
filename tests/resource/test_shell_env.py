from __future__ import annotations
import tempfile
import time
from pathlib import Path
import pytest
from praxile.config import Config
from praxile.environment import ShellEnv
from praxile.security import SafetyPolicy
from praxile.store import ExperienceStore

pytestmark = pytest.mark.resource

@pytest.mark.shell_resource
def test_safety_blocks_dangerous_command() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = Config.load(root)
        ExperienceStore(config.paths).initialize(config)
        shell = ShellEnv(config, SafetyPolicy(config))
        observation = shell.run("rm -rf .", timeout=5)
        assert observation.status == "blocked"


@pytest.mark.shell_resource
def test_shell_command_can_be_cancelled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "tests").mkdir()
        (root / "tests" / "test_sleep.py").write_text(
            "import time\n\ndef test_sleep():\n    time.sleep(10)\n",
            encoding="utf-8",
        )
        config = Config.load(root)
        ExperienceStore(config.paths).initialize(config)
        shell = ShellEnv(config, SafetyPolicy(config))
        started = time.monotonic()

        def cancel_requested() -> bool:
            return time.monotonic() - started > 0.25

        observation = shell.run("python -m pytest tests/test_sleep.py -q", timeout=10, cancel_requested=cancel_requested)
        assert observation.status == "cancelled"
        assert observation.data["cancelled"] is True
