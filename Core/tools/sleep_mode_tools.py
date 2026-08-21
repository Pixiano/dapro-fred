# Core/tools/sleep_mode_tools.py
#
# One tool: force-exit sleep mode on an explicit voice/text command
# ("cancel sleep mode", "wake up FRED"). See orchestrator/sleep_mode.py
# for the state machine — presence returning and a hotkey press already
# exit sleep mode on their own; this is the third, explicit way, for
# when neither of those happens to fire (e.g. Vatsal is in frame but the
# camera missed a match).

from orchestrator import sleep_mode


def cancel_sleep_mode() -> str:
    """Force FRED out of sleep mode right now, regardless of what
    presence currently reports."""
    if not sleep_mode.is_sleeping():
        return "I wasn't in sleep mode, sir."
    sleep_mode.wake("cancel_command")
    return "Sleep mode cancelled, sir."
