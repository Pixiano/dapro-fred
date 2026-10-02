# Lockdown mode: ToolRegistry.execute() is the single choke point every
# tool call passes through, so that's where the gate lives (see
# tools/registry.py) rather than a guard duplicated in every tool. The
# specific case this pins: lockdown_engage/lockdown_disengage must stay
# exempt from their own gate, or there's no way to ever unlock again.
#
# No popup — a native popup was tried and repeatedly failed on Windows'
# foreground-focus rules, not worth the fragility for a demo. Engaging
# stays a bare trigger; the PIN is only required to disengage, said
# together with that phrase ("unlock fred 1111").

from orchestrator.dispatcher import Dispatcher
from tools.registry import ToolRegistry
from tools import system_tools
from state import lockdown_state


def test_dispatcher_routes_lockdown_phrases():
    d = Dispatcher()
    for phrase in ("lockdown", "lockdown protocol", "engage lockdown"):
        assert d.match(phrase) == {"tool": "lockdown_engage", "arguments": {}}
    for phrase in ("unlock fred 1111", "lift lockdown 1111", "stand down 1111"):
        assert d.match(phrase) == {"tool": "lockdown_disengage", "arguments": {"pin": "1111"}}
    # Disengage with no PIN said at all -> no match, falls through to
    # the LLM path rather than silently trying with an empty PIN.
    assert d.match("unlock fred") is None


def test_engage_is_bare_disengage_requires_correct_pin(monkeypatch):
    # No PillApp running in a test — get_current_app() returns None, and
    # both tool functions must degrade to "just flip the state" rather
    # than raising.
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: None)
    # disengage also requires presence.is_present() now (2026-08-25 —
    # see system_tools.lockdown_disengage's own docstring) — pinned True
    # here so this test's "correct PIN" path doesn't silently depend on
    # whether Vatsal actually happens to be at his desk when it runs.
    monkeypatch.setattr("input.presence.is_present", lambda: True)
    lockdown_state.set_locked(False)
    try:
        result = system_tools.lockdown_engage()
        assert "engaged" in result.lower()
        assert lockdown_state.is_locked() is True

        # double-fire (small-model quirk observed live) is a no-op, not
        # a second engage
        assert "already" in system_tools.lockdown_engage().lower()

        wrong = system_tools.lockdown_disengage(pin="0000")
        assert "wrong pin" in wrong.lower()
        assert lockdown_state.is_locked() is True

        result = system_tools.lockdown_disengage(pin="1111")
        assert "lifted" in result.lower()
        assert lockdown_state.is_locked() is False

        assert "nothing to lift" in system_tools.lockdown_disengage(pin="1111").lower()
    finally:
        lockdown_state.set_locked(False)


def test_disengage_refuses_even_correct_pin_without_presence(monkeypatch):
    # The PIN alone must not be enough — a stranger who knows "1111"
    # (checked into git, not a real secret) is exactly who this gate is
    # for. See system_tools.lockdown_disengage's own docstring.
    monkeypatch.setattr("ui.pill_app.get_current_app", lambda: None)
    monkeypatch.setattr("input.presence.is_present", lambda: False)
    lockdown_state.set_locked(True)
    try:
        result = system_tools.lockdown_disengage(pin="1111")
        assert "can't confirm" in result.lower()
        assert lockdown_state.is_locked() is True
    finally:
        lockdown_state.set_locked(False)


def test_registry_gate_blocks_other_tools_but_exempts_lockdown_tools():
    lockdown_state.set_locked(False)
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
            name="lockdown_engage",
            function=lambda: "engaged",
            description="test",
            parameters={"type": "object", "properties": {}},
        )
        registry.register(
            name="lockdown_disengage",
            function=lambda: "disengaged",
            description="test",
            parameters={"type": "object", "properties": {}},
        )

        assert registry.execute("dummy") == "did the thing"

        lockdown_state.set_locked(True)

        # the exemption: both lockdown tools stay callable while locked,
        # or there's no way to ever unlock again
        assert registry.execute("lockdown_engage") == "engaged"
        assert registry.execute("lockdown_disengage") == "disengaged"

        blocked = registry.execute("dummy")
        assert "lockdown" in blocked.lower()
        assert len(calls) == 1  # dummy did NOT run while locked

        lockdown_state.set_locked(False)
        assert registry.execute("dummy") == "did the thing"
        assert len(calls) == 2
    finally:
        lockdown_state.set_locked(False)
