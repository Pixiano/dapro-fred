# Core/tools/security_tools.py
#
# confirm_lockdown_lift: the ONLY way orchestrator/security_watch.py's
# own stranger-detection lockdown can be lifted by voice — reachable
# only via the primed ask-and-answer security_watch._on_wake() sets up
# (see that module's docstring). The security property ("recognition
# alone isn't enough to lift a lockdown") comes from requiring this
# explicit tool call to fire at all — only reachable through the primed
# carry-forward, never a bare LLM guess — not from weakening
# system_tools.lockdown_disengage's own PIN check below, which runs
# unchanged.
#
# Separate module from system_tools.py rather than added there: this
# needs to read/clear orchestrator/security_watch.py's own ask-state
# (_asked_at, _locked_by_stranger_event), and security_watch.py already
# imports system_tools for lockdown_engage — putting this tool in
# system_tools.py would cycle back through security_watch.

from tools import system_tools


def confirm_lockdown_lift(confirmed: bool) -> str:
    """Answers security_watch.py's own 'Welcome back, sir — lift
    lockdown?' ask. Outside the 5-minute answer window: stays locked,
    does NOT auto-lift and does NOT re-ask — falls back to manual PIN,
    per the module's own no-nagging rule."""
    from orchestrator import security_watch

    if security_watch._asked_at is None:
        return "Nothing pending, sir."

    if not security_watch.ask_window_open():
        security_watch._asked_at = None
        return "That window's closed, sir — still locked. Use the PIN to unlock manually."

    security_watch._asked_at = None

    if not confirmed:
        return "Staying locked, sir."

    result = system_tools.lockdown_disengage(pin=system_tools._LOCKDOWN_PIN)
    if "lifted" in result.lower():
        security_watch._locked_by_stranger_event = False
    return result
