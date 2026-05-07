from __future__ import annotations

import faulthandler
import gc
import os
import sys
import threading

import pytest


RESOURCE_MARKER_RULES: tuple[tuple[str, tuple[str, ...], int, bool], ...] = (
    ("shell_resource", ("shell_", "subprocess", "tee_exception"), 10, True),
    ("gateway_resource", ("gateway_",), 20, True),
    ("browser_resource", ("browser_",), 20, True),
    ("terminal_resource", ("terminal_session",), 20, True),
    ("runtime_resource", ("runtime_", "batch_action", "checkpoint", "resume_after_interruption"), 20, True),
    ("http_resource", ("http_transport", "httpx_transport"), 10, True),
    ("sqlite_resource", ("store_", "reindex", "index_", "retrieval", "trajectory"), 20, False),
)

@pytest.fixture(autouse=True)
def praxile_isolated_process_state():
    old_cwd = os.getcwd()
    old_env = dict(os.environ)
    old_threads = {thread.ident for thread in threading.enumerate()}
    try:
        yield
    finally:
        gc.collect()
        leaked_threads: list[str] = []
        for thread in threading.enumerate():
            if thread.ident in old_threads or thread.daemon or not thread.is_alive():
                continue
            thread.join(timeout=1.0)
            if thread.is_alive():
                leaked_threads.append(thread.name)
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)
        if leaked_threads:
            pytest.fail(f"Non-daemon thread(s) leaked from test: {', '.join(sorted(leaked_threads))}")


def _timeout_for_node(request: pytest.FixtureRequest) -> float:
    timeout_marker = request.node.get_closest_marker("timeout")
    if timeout_marker and timeout_marker.args:
        try:
            return float(timeout_marker.args[0])
        except (TypeError, ValueError):
            pass
    override = os.environ.get("PRAXILE_TEST_TIMEOUT_SECONDS")
    if override is not None:
        try:
            return float(override)
        except ValueError:
            return 15.0
    if request.node.get_closest_marker("slow") or request.node.get_closest_marker("integration"):
        return 60.0
    return 15.0


@pytest.fixture(autouse=True)
def praxile_hang_watchdog(request: pytest.FixtureRequest):
    pluginmanager = request.config.pluginmanager
    if pluginmanager.hasplugin("timeout") or pluginmanager.hasplugin("pytest_timeout"):
        yield
        return
    timeout = _timeout_for_node(request)
    if timeout <= 0:
        yield
        return
    faulthandler.dump_traceback_later(timeout, repeat=False, file=sys.stderr, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        # \u79fb\u9664\u901a\u8fc7\u6d4b\u8bd5\u540d\u81ea\u52a8\u52a0 marker \u7684\u903b\u8f91\u3002\u8981\u6c42\u5f00\u53d1\u8005\u663e\u5f0f\u52a0 @pytest.mark.resource
        # \u5982\u679c\u4f9d\u7136\u60f3\u4fdd\u7559 timeout \u5206\u914d\uff0c\u53ef\u4ee5\u901a\u8fc7\u67e5\u627e\u5df2\u7ecf\u5b58\u5728\u7684 marker \u6765\u5206\u914d
        resource_timeouts: list[int] = []
        force_integration = False
        
        for marker_name, patterns, timeout_seconds, marker_force_integration in RESOURCE_MARKER_RULES:
            if item.get_closest_marker(marker_name):
                resource_timeouts.append(timeout_seconds)
                force_integration = force_integration or marker_force_integration
                
        if resource_timeouts and not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(max(resource_timeouts)))
        if force_integration and not item.get_closest_marker("integration"):
            item.add_marker("integration")
