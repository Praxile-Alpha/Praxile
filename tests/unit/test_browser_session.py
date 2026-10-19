from types import SimpleNamespace

import pytest

from praxile.browser import BrowserEnv
from praxile.config import Config, ProjectPaths, default_config
from praxile.tools import ToolRegistry
from praxile.action_schema import ActionSchemaRegistry


@pytest.fixture
def session(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    config.data["browser"].update(enabled=True, allow_interactions=True)
    env = BrowserEnv(config)

    class Page:
        url = "about:blank"
        value = ""
        def goto(self, url, **kwargs):
            self.url = url
        def locator(self, selector):
            return self
        def click(self, **kwargs):
            self.value += "!"
        def fill(self, value, **kwargs):
            self.value = value
        def inner_text(self, **kwargs):
            return self.value

    class Context:
        closed = False
        page = Page()
        def route(self, pattern, callback):
            self.handler = callback
        def route_web_socket(self, pattern, callback):
            self.socket_handler = callback
        def new_page(self):
            return self.page
        def close(self):
            self.closed = True

    context = Context()
    env._browser_instance = SimpleNamespace(new_context=lambda **kwargs: context, close=lambda: None)
    yield config, env, context
    env.close()


def test_runtime_tools_share_state_and_assertions(session):
    config, env, context = session
    tools = ToolRegistry(config, browser=env)
    actions = [
        {"type": "browser_navigate", "url": "http://localhost:8000"},
        {"type": "browser_fill", "selector": "input", "value": "hello"},
        {"type": "browser_click", "selector": "button"},
        {"type": "browser_assert_text", "selector": "output", "value": "hello!"},
    ]
    for step, action in enumerate(actions):
        assert ActionSchemaRegistry().validate(action)[0]
        assert tools.execute(action, task_id="session", step=step)["status"] == "success"
    assert env.interact("assert_text", selector="output", value="wrong").status == "failure"
    env.close()
    assert context.closed


def test_observation_never_starts_browser_and_strips_url_secrets(tmp_path):
    config = Config(default_config(tmp_path), ProjectPaths(tmp_path))
    env = BrowserEnv(config, sync_playwright_factory=lambda: pytest.fail("must not launch"))
    assert env.observe_state() == {"session_active": False}
    env._browser_instance = SimpleNamespace(is_connected=lambda: True, version="123.4", close=lambda: None)
    env._session_page = SimpleNamespace(is_closed=lambda: False,
        url="https://user:password@example.com/private/token?secret=key#fragment")
    assert env.observe_state() == {"session_active": True, "chromium_version": "123.4",
                                   "origin": "https://example.com:443"}
    env._session_page.url = "http://[::1]:8000/path"
    assert env.observe_state()["origin"] == "http://[::1]:8000"
    env._session_page.is_closed = lambda: True
    assert env.observe_state() == {"session_active": False, "chromium_version": "123.4"}
    env.close()


def test_observation_error_remains_unknown(session):
    _, env, _ = session
    def disconnected():
        raise RuntimeError("private transport detail")
    env._browser_instance.is_connected = disconnected
    assert env.observe_state() == {}


def test_requires_optin_session_and_explicit_hosts(session):
    config, env, _ = session
    assert env.interact("click", selector="button").status == "blocked"
    config.data["browser"]["allow_interactions"] = False
    assert env.interact("navigate", url="http://localhost").status == "blocked"
    config.data["browser"]["allow_interactions"] = True
    config.data["browser"]["allowed_hosts"] = []
    assert env.interact("navigate", url="http://localhost").status == "blocked"


@pytest.mark.parametrize("url,allowed", [("http://localhost/a", True),
    ("https://evil.example/", False), ("file:///etc/passwd", False),
    ("http://secret@localhost/", False)])
def test_subrequests_and_redirects_are_checked(session, url, allowed):
    _, env, _ = session
    events = []
    route = SimpleNamespace(request=SimpleNamespace(url=url),
        abort=lambda: events.append("abort"), continue_=lambda: events.append("continue"))
    env._route_session_request(route)
    assert events == (["continue"] if allowed else ["abort"])


def test_error_is_sanitized_and_session_closed(session):
    _, env, context = session
    assert env.interact("navigate", url="http://localhost").status == "success"
    def fail(**kwargs):
        raise RuntimeError("typed secret")
    context.page.click = fail
    observation = env.interact("click", selector="button")
    assert observation.status == "failure"
    assert "typed secret" not in str(observation.to_dict())
    assert context.closed
    assert env._session_page is None


def test_playwright_is_released_even_when_browser_close_raises(session):
    _, env, _ = session
    released = []
    env._playwright_context = SimpleNamespace(__exit__=lambda *args: released.append(True))
    def fail():
        raise RuntimeError("close failure")
    env._browser_instance.close = fail
    with pytest.raises(RuntimeError, match="close failure"):
        env.close()
    assert released == [True]
    assert env._closed
