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

import threading

from config.settings import PRESENCE_ABSENT_DEBOUNCE, PRESENCE_PRESENT_DEBOUNCE
from orchestrator import consolidation, reflection
from utils import event_log

_streak = 0  # consecutive absent polls
_present_streak = 0  # consecutive present/match polls, mirrors _streak
_sleeping = False


def is_sleeping() -> bool:
    return _sleeping


def on_presence_poll(present: bool, greeting: str = None):
    """Feed one presence.poll_once() result in. Called right after every
    poll in proactive_checks.check_presence().

    greeting: optional wake-greeting text, forwarded to
    consolidation.on_sleep_exit() so it's folded into the same bundled
    notify() call instead of firing as a second, competing one — see
    on_sleep_exit's docstring. Only used on the actual presence-return
    edge below; ignored otherwise."""
    global _streak, _present_streak, _sleeping

    if present:
        _streak = 0
        _present_streak += 1
        # Debounced the same way absence is: a single confident-but-wrong
        # frame must not immediately exit sleep mode / fire the wake
        # greeting (see PRESENCE_MATCH_THRESHOLD_HIGH's comment on why).
        if _sleeping and _present_streak >= PRESENCE_PRESENT_DEBOUNCE:
            _sleeping = False
            event_log.log("sleep_mode_exit", reason="presence_returned")
            consolidation.on_sleep_exit(greeting=greeting)
        return

    _present_streak = 0
    _streak += 1
    if not _sleeping and _streak >= PRESENCE_ABSENT_DEBOUNCE:
        _sleeping = True
        event_log.log("sleep_mode_enter", streak=_streak)
        # Off-thread: this call chain (consolidation.on_sleep_enter(), an
        # LLM call, then reflection.run_if_due(), a multi-chunk LLM pass
        # that can run for minutes) used to run inline here, which is
        # inside proactive_checks.check_presence()'s periodic scheduler
        # job — APScheduler won't fire that job's next 15s tick until the
        # current one returns, so this blocked presence polling entirely
        # for as long as reflection took. Confirmed live 2026-08-23: a
        # multi-minute gap in presence_log.jsonl right as reflection
        # kicked in, no wake greeting until the hotkey forced it. It also
        # made reflection's own "check is_sleeping() between chunks, bail
        # out if presence returned" interrupt logic a no-op, since
        # nothing could ever flip that flag while this call held the only
        # thread that could flip it.
        threading.Thread(target=_run_sleep_enter_work, daemon=True).start()


def _run_sleep_enter_work():
    consolidation.on_sleep_enter()
    # Own trigger gate (accumulated new material, not sleep-mode
    # entry itself) — most sleep windows are a no-op here. See
    # reflection.py's module docstring for the full story.
    consolidation.append_pending(reflection.run_if_due())


def wake(reason: str):
    """Force-exit sleep mode regardless of the current streak — the
    hotkey handler and the cancel-sleep-mode tool both call this."""
    global _streak, _present_streak, _sleeping
    _streak = 0
    _present_streak = 0
    if _sleeping:
        _sleeping = False
        event_log.log("sleep_mode_exit", reason=reason)
        consolidation.on_sleep_exit()
