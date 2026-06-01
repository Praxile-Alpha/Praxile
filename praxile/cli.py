from __future__ import annotations

"""Public CLI entry point for the split Praxile command package."""

from typing import Any

from . import cli_common as _common
from . import cli_commands as _commands
from .cli_commands import assets as _cmd_assets
from .cli_commands import context_policy as _cmd_context_policy
from .cli_commands import evals as _cmd_evals
from .cli_commands import feedback_reflect as _cmd_feedback_reflect
from .cli_commands import graph_audit as _cmd_graph_audit
from .cli_commands import index_search as _cmd_index_search
from .cli_commands import models_tools as _cmd_models_tools
from .cli_commands import run_review as _cmd_run_review
from .cli_commands import setup as _cmd_setup
from .cli_commands import spec as _cmd_spec
from .cli_common import *  # noqa: F401,F403
from .cli_main import main as _main
from .cli_parser import build_parser
from .cli_commands import *  # noqa: F401,F403

_COMMAND_MODULES = [
    _cmd_setup,
    _cmd_run_review,
    _cmd_spec,
    _cmd_graph_audit,
    _cmd_context_policy,
    _cmd_feedback_reflect,
    _cmd_index_search,
    _cmd_models_tools,
    _cmd_assets,
    _cmd_evals,
]
_COMMAND_WRAPPERS: dict[str, Any] = {}


def _sync_overrides() -> None:
    for name, value in globals().items():
        if name.startswith("__") or name in {
            "Any",
            "_COMMAND_MODULES",
            "_COMMAND_WRAPPERS",
            "_commands",
            "_common",
            "_main",
            "_sync_overrides",
            "_wrap_command",
            "build_parser",
            "main",
        }:
            continue
        if _COMMAND_WRAPPERS.get(name) is value:
            continue
        if hasattr(_common, name):
            setattr(_common, name, value)
        for module in _COMMAND_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


def _wrap_command(name: str):
    for module in _COMMAND_MODULES:
        if hasattr(module, name):
            original = getattr(module, name)
            break
    else:
        raise AttributeError(name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _sync_overrides()
        return getattr(original, "__globals__", {}).get(name, original)(*args, **kwargs)

    wrapper.__name__ = name
    wrapper.__doc__ = getattr(original, "__doc__", None)
    wrapper.__module__ = __name__
    _COMMAND_WRAPPERS[name] = wrapper
    return wrapper


for _name in [name for name in dir(_commands) if name.startswith("cmd_")]:
    globals()[_name] = _wrap_command(_name)


def main(argv: list[str] | None = None) -> int:
    _sync_overrides()
    return _main(argv)


__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    raise SystemExit(main())
