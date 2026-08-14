# Lockdown mode: ToolRegistry.execute() is the single choke point every
# tool call passes through, so that's where the gate lives (see
# tools/registry.py) rather than a guard duplicated in every tool. The
# one way this breaks quietly is the "lockdown" tool itself getting
# caught by its own gate, leaving no way to ever say "unlock" again —
# that's the specific case this test exists to pin.

from orchestrator.dispatcher import Dispatcher
from tools.registry import ToolRegistry
from tools import system_tools
from state import lockdown_state


def test_dispatcher_routes_lockdown_phrases():
    d = Dispatcher()
    assert d.match("lockdown") == {"tool": "lockdown", "arguments": {"should_lock": True}}
    assert d.match("unlock") == {"tool": "lockdown", "arguments": {"should_lock": False}}
    assert d.match("stand down") == {"tool": "lockdown", "arguments": {"should_lock": False}}


def test_registry_gate_blocks_other_tools_but_exempts_lockdown():
    lockdown_state.set_locked(False)  # don't leak state from a prior test
    try:
        calls = []
        registry = ToolRegistry()
        registry.register(
            name="dummy",
            function=lambda: calls.append(1) or "did the thing",
            description="test tool",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(
            name="lockdown",
            function=system_tools.lockdown,
            description="test lockdown tool",
            parameters={"type": "object", "properties": {}},
        )

        assert registry.execute("dummy") == "did the thing"

        assert registry.execute("lockdown", should_lock=True) == "Lockdown engaged, sir."
        assert lockdown_state.is_locked() is True

        blocked = registry.execute("dummy")
        assert "lockdown" in blocked.lower()
        assert len(calls) == 1  # dummy did NOT run the second time

        # the exemption: lockdown must stay callable while locked, or
        # there's no way to ever unlock again
        assert registry.execute("lockdown", should_lock=False) == "Lockdown lifted, sir."
        assert lockdown_state.is_locked() is False

        assert registry.execute("dummy") == "did the thing"
        assert len(calls) == 2
    finally:
        lockdown_state.set_locked(False)
