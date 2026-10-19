from praxile.browser import BrowserEnv
from praxile.browser_download import download
from praxile.config import Config, ProjectPaths, default_config
from praxile.tools import MUTATING_ACTIONS
from praxile.action_schema import ActionSchemaRegistry


def test_download_opt_in_and_explicit_hosts_are_required(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    browser = BrowserEnv(config)
    assert download(browser, "https://example.com/file").status == "blocked"
    config.data["browser"].update(enabled=True, allow_downloads=True, allowed_hosts=[])
    assert download(browser, "https://example.com/file").status == "blocked"
    config.data["browser"]["allowed_hosts"] = ["example.com"]
    assert download(browser, "https://user:secret@example.com/file").status == "blocked"
    assert "browser_download" in MUTATING_ACTIONS
    assert ActionSchemaRegistry().validate({"type": "browser_download", "url": "https://example.com/file"})[0]


def test_download_rejects_symlink_escape_before_network(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".praxile").symlink_to(outside, target_is_directory=True)
    config = Config(default_config(project), ProjectPaths(project))
    config.data["browser"].update(enabled=True, allow_downloads=True, allowed_hosts=["example.com"])
    assert download(BrowserEnv(config), "https://example.com/file").status == "blocked"
    assert list(outside.iterdir()) == []
