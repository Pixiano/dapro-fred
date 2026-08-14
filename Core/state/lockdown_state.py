# Core/state/lockdown_state.py
#
# FRED's lockdown flag — while set, ToolRegistry.execute() (see
# tools/registry.py) refuses every tool call except lockdown() itself.
# Conversation still works; FRED just can't act on anything.
#
# A bare module-level bool, not a class: there is exactly one FRED
# process and exactly one lockdown state — same shape as
# audio/mute_state.py, no per-instance reason to wrap it.

_locked = False


def set_locked(value: bool) -> None:
    global _locked
    _locked = bool(value)


def is_locked() -> bool:
    return _locked
