from __future__ import annotations
import tempfile
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
