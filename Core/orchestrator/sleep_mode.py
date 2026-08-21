# Core/orchestrator/sleep_mode.py
#
# Sleep-mode state machine — sections 2-4 of
# fred-presence-sleep-mode-plan_2026-08-18.md. Presence detection itself
# (input/presence.py) is done and just reports a raw per-poll camera
# result; this module is what turns "N misses in a row" into an actual
# sleep-mode decision, and gates proactive nudges on it.
#
# In-memory only, deliberately — a restart is itself a real,
# presence-independent event (screen watcher, scheduler etc. all
# reinitialize fresh too), so there's no clear reason sleep-mode needs to
# survive one. If that turns out wrong, follow presence.py's own
# STATE_PATH/_save_state pattern.

from config.settings import PRESENCE_ABSENT_DEBOUNCE
from orchestrator import consolidation, reflection
from utils import event_log

_streak = 0  # consecutive absent polls
_sleeping = False


def is_sleeping() -> bool:
    return _sleeping


def on_presence_poll(present: bool):
    """Feed one presence.poll_once() result in. Called right after every
    poll in proactive_checks.check_presence()."""
    global _streak, _sleeping

    if present:
        _streak = 0
        if _sleeping:
            _sleeping = False
            event_log.log("sleep_mode_exit", reason="presence_returned")
            consolidation.on_sleep_exit()
        return

    _streak += 1
    if not _sleeping and _streak >= PRESENCE_ABSENT_DEBOUNCE:
        _sleeping = True
        event_log.log("sleep_mode_enter", streak=_streak)
        consolidation.on_sleep_enter()
        # Own trigger gate (accumulated new material, not sleep-mode
        # entry itself) — most sleep windows are a no-op here. See
        # reflection.py's module docstring for the full story.
        consolidation.append_pending(reflection.run_if_due())


def wake(reason: str):
    """Force-exit sleep mode regardless of the current streak — the
    hotkey handler and the cancel-sleep-mode tool both call this."""
    global _streak, _sleeping
    _streak = 0
    if _sleeping:
        _sleeping = False
        event_log.log("sleep_mode_exit", reason=reason)
        consolidation.on_sleep_exit()
