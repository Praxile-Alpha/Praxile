# Contributing Testing

Praxile keeps fast tests and resource-sensitive tests separate. This prevents shell, runtime, SQLite, browser, gateway, terminal, and HTTP-client checks from hiding leaks inside the default unit loop.

## Test Groups

Run the fast core loop while developing ordinary code:

```bash
make test-fast
make test-fast-repeat
```

Run resource-sensitive checks in isolated subprocesses:

```bash
make test-resource
```

Run slow or integration checks in isolated subprocesses:

```bash
make test-integration
```

Run the full local release-style suite:

```bash
make test-release
```

`test-resource`, `test-integration`, and `test-full` require the dev extra because they use `pytest-forked`:

```bash
python -m pip install -e ".[dev]"
```

## Marker Rules

Resource tests must live under `tests/resource/` and use explicit markers. Do not rely on test names to auto-classify resource tests.

Use:

```python
pytestmark = pytest.mark.resource

@pytest.mark.shell_resource
def test_shell_timeout_branch():
    ...
```

Use the most specific marker that applies:

- `shell_resource` for subprocess or shell behavior.
- `gateway_resource` for gateway app or server lifecycle.
- `browser_resource` for browser context lifecycle.
- `terminal_resource` for interactive terminal behavior.
- `runtime_resource` for model action loops, checkpoints, or runtime execution.
- `http_resource` for HTTP client or streaming transport lifecycle.
- `sqlite_resource` for store, index, retrieval, or trajectory persistence.

## Hang Debugging

Start with:

```bash
python -B -m pytest -vv --durations=30 -m "not slow and not integration"
python -B -m pytest -vv --durations=30 -m "resource"
python -B -m pytest -vv --durations=30 -m "slow or integration"
```

If a hang appears, run only the marker group in forked mode:

```bash
python -B -m pytest -q -m "resource" --forked --timeout=60 --timeout-method=thread
```

The local test watchdog uses `PRAXILE_TEST_TIMEOUT_SECONDS` when `pytest-timeout` is not installed:

```bash
PRAXILE_TEST_TIMEOUT_SECONDS=15 python -B -m pytest -q -m "not slow and not integration"
```

## Cleanup Expectations

Every resource test must close what it opens:

- call `ToolRegistry.close()` or use a context manager for browser-backed tools;
- close HTTP transports with `.close()` or a context manager;
- keep SQLite connections scoped to store methods/context managers;
- give every shell command an explicit timeout;
- verify timeout branches release process groups and pipes.
