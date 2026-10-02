# Core/tests/test_registry.py
#
# Regression test for the lockdown_disengage(pin=1111) crash confirmed
# live 2026-08-25 — a spoken PIN arrived from the tool-call JSON as an
# int despite the "pin" parameter being declared type "string", and the
# tool crashed calling .strip() on it. ToolRegistry.execute now coerces
# string-typed args before calling the tool function; this checks that
# coercion, not any specific tool.

from state import lockdown_state
from tools.registry import ToolRegistry


def test_execute_coerces_int_to_declared_string_type():
    lockdown_state.set_locked(False)  # execute()'s lockdown gate must not intercept this test tool
    registry = ToolRegistry()
    registry.register(
        name="echo_pin",
        function=lambda pin: pin.strip(),
        description="test tool",
        parameters={"type": "object", "properties": {"pin": {"type": "string"}}},
    )
    assert registry.execute("echo_pin", pin=1111) == "1111"
    assert registry.execute("echo_pin", pin="1111") == "1111"


def test_execute_leaves_non_string_typed_args_alone():
    lockdown_state.set_locked(False)
    registry = ToolRegistry()
    registry.register(
        name="echo_count",
        function=lambda count: count + 1,
        description="test tool",
        parameters={"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    assert registry.execute("echo_count", count=5) == 6
