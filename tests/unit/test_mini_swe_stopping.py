from __future__ import annotations

from praxile.adapters.mini_swe_stopping import MiniSweStoppingMonitor, MiniSweStoppingPolicy


def _trajectory(commands: list[str], returncodes: list[int] | None = None) -> dict:
    messages = []
    returncodes = returncodes or [0] * len(commands)
    for index, (command, returncode) in enumerate(zip(commands, returncodes)):
        messages.append(
            {
                "role": "assistant",
                "extra": {"actions": [{"id": f"call-{index}", "command": command}]},
            }
        )
        messages.append(
            {"role": "tool", "content": f'<returncode>{returncode}</returncode>'}
        )
    return {"messages": messages}


def test_exploration_budget_hard_stops_without_patch() -> None:
    monitor = MiniSweStoppingMonitor(
        MiniSweStoppingPolicy(enabled=True, max_steps_without_patch=3)
    )

    decision = monitor.evaluate(_trajectory(["pwd", "ls", "rg parser"]), patch_present=False)

    assert decision is not None
    assert decision.reason == "exploration_budget_exhausted"
    assert decision.action_count == 3


def test_verified_patch_stops_after_grace_steps() -> None:
    monitor = MiniSweStoppingMonitor(
        MiniSweStoppingPolicy(enabled=True, max_steps_after_patch=20, verified_grace_steps=2)
    )
    assert monitor.evaluate(_trajectory(["apply_patch"]), patch_present=True) is None

    decision = monitor.evaluate(
        _trajectory(["apply_patch", "pytest -q", "git diff"]),
        patch_present=True,
    )

    assert decision is not None
    assert decision.reason == "patch_verified"
    assert decision.successful_verification_action == 2


def test_repeated_verification_command_is_stopped() -> None:
    monitor = MiniSweStoppingMonitor(
        MiniSweStoppingPolicy(
            enabled=True,
            max_steps_after_patch=20,
            verified_grace_steps=20,
            repeated_command_limit=3,
        )
    )
    monitor.evaluate(_trajectory(["apply_patch"]), patch_present=True)

    decision = monitor.evaluate(
        _trajectory(["apply_patch", "pytest -q", "pytest -q", "pytest -q"]),
        patch_present=True,
    )

    assert decision is not None
    assert decision.reason == "repeated_action_after_verification"


def test_disabled_policy_never_stops() -> None:
    monitor = MiniSweStoppingMonitor(MiniSweStoppingPolicy(enabled=False))
    assert monitor.evaluate(_trajectory(["pwd"] * 200), patch_present=False) is None
