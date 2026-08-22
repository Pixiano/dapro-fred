# Core/tests/test_security_watch.py
#
# Pure logic tests for orchestrator/security_watch.py: streak debounce,
# lockdown-engage trigger, already-locked-by-Vatsal exclusion, and the
# wake-edge lift-lockdown ask + its 5-minute timeout. Camera, lockdown
# state, notify, presence, and sleep_mode are all mocked — same shape as
# test_sleep_mode.py/test_consolidation.py.

from datetime import datetime, timedelta

from orchestrator import security_watch
from input import presence
from state import lockdown_state


def _reset(monkeypatch):
    security_watch._stranger_streak = 0
    security_watch._fired_this_episode = False
    security_watch._locked_by_stranger_event = False
    security_watch._asked_at = None
    security_watch._was_sleeping = False

    monkeypatch.setattr(security_watch, "_burst_save_photos", lambda: 3)
    monkeypatch.setattr(security_watch.event_log, "log", lambda *a, **k: None)
    monkeypatch.setattr(security_watch.event_log, "log_error", lambda *a, **k: None)

    appended = []
    monkeypatch.setattr(
        "orchestrator.consolidation.append_pending",
        lambda text: appended.append(text),
    )

    import orchestrator.sleep_mode as sleep_mode
    monkeypatch.setattr(sleep_mode, "is_sleeping", lambda: False)

    import orchestrator.proactive_checks as proactive_checks
    monkeypatch.setattr(proactive_checks, "idle_seconds", lambda: 0.0)

    monkeypatch.setattr(presence, "is_present", lambda: False)
    monkeypatch.setattr(presence, "last_classification", lambda: {"known_people": [], "unrecognized": True})

    monkeypatch.setattr(lockdown_state, "is_locked", lambda: False)

    engaged = []
    monkeypatch.setattr(security_watch.system_tools, "lockdown_engage", lambda: engaged.append(True))

    return appended, engaged


# =========================================================
# STRANGER-STREAK DEBOUNCE + LOCKDOWN-ENGAGE TRIGGER
# =========================================================

def test_streak_below_debounce_does_not_engage(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    for _ in range(security_watch.SECURITY_STRANGER_DEBOUNCE - 1):
        security_watch._check()
    assert engaged == []
    assert appended == []


def test_streak_reaches_debounce_engages_lockdown_once(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    for _ in range(security_watch.SECURITY_STRANGER_DEBOUNCE):
        security_watch._check()
    assert engaged == [True]
    assert len(appended) == 1
    assert "unrecognized person" in appended[0]
    assert security_watch._locked_by_stranger_event is True

    # Still triggering — must not re-fire within the same episode.
    security_watch._check()
    assert engaged == [True]


def test_condition_clearing_resets_episode_for_a_future_trigger(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    for _ in range(security_watch.SECURITY_STRANGER_DEBOUNCE):
        security_watch._check()
    assert engaged == [True]

    monkeypatch.setattr(presence, "is_present", lambda: True)  # Vatsal's back — condition clears
    security_watch._check()
    assert security_watch._stranger_streak == 0
    assert security_watch._fired_this_episode is False

    monkeypatch.setattr(presence, "is_present", lambda: False)
    for _ in range(security_watch.SECURITY_STRANGER_DEBOUNCE):
        security_watch._check()
    assert engaged == [True, True]  # a genuinely new episode fired again


def test_already_locked_by_vatsal_is_not_claimed(monkeypatch):
    """If lockdown was already engaged before the stranger trigger, this
    feature must never later offer to lift it — that's Vatsal's own
    lockdown for unrelated reasons."""
    appended, engaged = _reset(monkeypatch)
    monkeypatch.setattr(lockdown_state, "is_locked", lambda: True)

    for _ in range(security_watch.SECURITY_STRANGER_DEBOUNCE):
        security_watch._check()

    assert engaged == [True]  # still engages (no-op inside lockdown_engage in real code, mocked here)
    assert security_watch._locked_by_stranger_event is False


# =========================================================
# WAKE EDGE: greeting + one-time lift-lockdown ask
# =========================================================

def test_wake_edge_asks_once_when_locked_by_stranger_event(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    security_watch._locked_by_stranger_event = True
    monkeypatch.setattr(lockdown_state, "is_locked", lambda: True)

    notified = []
    monkeypatch.setattr(security_watch, "notify", lambda *a, **k: notified.append(a))
    primed = []
    security_watch.configure(prime_carry=lambda names: primed.append(names))

    import orchestrator.sleep_mode as sleep_mode
    monkeypatch.setattr(sleep_mode, "is_sleeping", lambda: True)
    security_watch._was_sleeping = False
    security_watch._check()  # still sleeping this tick — no edge yet
    assert notified == []

    monkeypatch.setattr(sleep_mode, "is_sleeping", lambda: False)
    security_watch._check()  # True -> False edge fires the ask
    assert len(notified) == 1
    assert primed == [["confirm_lockdown_lift"]]
    assert security_watch._asked_at is not None

    security_watch.configure(prime_carry=None)


def test_wake_edge_does_not_ask_when_not_locked_by_us(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    monkeypatch.setattr(lockdown_state, "is_locked", lambda: True)  # locked, but not by this feature

    notified = []
    monkeypatch.setattr(security_watch, "notify", lambda *a, **k: notified.append(a))

    import orchestrator.sleep_mode as sleep_mode
    security_watch._was_sleeping = True
    monkeypatch.setattr(sleep_mode, "is_sleeping", lambda: False)
    security_watch._check()

    assert notified == []
    assert security_watch._asked_at is None


def test_wake_edge_greets_recognized_family_members(monkeypatch):
    appended, engaged = _reset(monkeypatch)
    monkeypatch.setattr(presence, "last_classification", lambda: {"known_people": ["Mom"], "unrecognized": False})
    monkeypatch.setattr(security_watch, "_family_greet_enabled", lambda name: True)

    import orchestrator.sleep_mode as sleep_mode
    security_watch._was_sleeping = True
    monkeypatch.setattr(sleep_mode, "is_sleeping", lambda: False)
    security_watch._check()

    assert any("Mom" in text for text in appended)


# =========================================================
# confirm_lockdown_lift TOOL — 5-minute ask-timeout
# =========================================================

def test_confirm_lockdown_lift_after_timeout_stays_locked(monkeypatch):
    from tools import security_tools

    security_watch._asked_at = datetime.now() - timedelta(minutes=6)
    security_watch._locked_by_stranger_event = True
    monkeypatch.setattr(lockdown_state, "is_locked", lambda: True)

    result = security_tools.confirm_lockdown_lift(True)

    assert "closed" in result.lower()
    assert security_watch._asked_at is None
    assert security_watch._locked_by_stranger_event is True  # untouched -- still locked


def test_confirm_lockdown_lift_within_window_lifts_with_pin(monkeypatch):
    from tools import security_tools

    security_watch._asked_at = datetime.now()
    security_watch._locked_by_stranger_event = True
    monkeypatch.setattr(lockdown_state, "is_locked", lambda: True)

    disengaged = []

    def _fake_disengage(pin=""):
        disengaged.append(pin)
        return "Lockdown lifted, sir."

    monkeypatch.setattr(security_tools.system_tools, "lockdown_disengage", _fake_disengage)

    result = security_tools.confirm_lockdown_lift(True)

    assert "lifted" in result.lower()
    assert disengaged == [security_tools.system_tools._LOCKDOWN_PIN]
    assert security_watch._locked_by_stranger_event is False


def test_confirm_lockdown_lift_declined_stays_locked_without_pin_call(monkeypatch):
    from tools import security_tools

    security_watch._asked_at = datetime.now()
    security_watch._locked_by_stranger_event = True

    def _boom(pin=""):
        raise AssertionError("lockdown_disengage should not be called on a decline")

    monkeypatch.setattr(security_tools.system_tools, "lockdown_disengage", _boom)

    result = security_tools.confirm_lockdown_lift(False)

    assert "staying locked" in result.lower()
    assert security_watch._locked_by_stranger_event is True


if __name__ == "__main__":
    print("run via pytest: python -m pytest tests/test_security_watch.py")
