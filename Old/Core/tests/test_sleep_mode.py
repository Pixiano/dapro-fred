# Core/tests/test_sleep_mode.py
#
# Pure unit tests against the streak/state machine in
# orchestrator/sleep_mode.py — no camera/hotkey I/O, just booleans in.

from orchestrator import sleep_mode


def _reset():
    sleep_mode._streak = 0
    sleep_mode._present_streak = 0
    sleep_mode._sleeping = False


def _enter_sleep():
    for _ in range(sleep_mode.PRESENCE_ABSENT_DEBOUNCE):
        sleep_mode.on_presence_poll(False)
    assert sleep_mode.is_sleeping()


def test_streak_reaches_debounce_enters_sleep_mode():
    _reset()
    for _ in range(sleep_mode.PRESENCE_ABSENT_DEBOUNCE - 1):
        sleep_mode.on_presence_poll(False)
        assert not sleep_mode.is_sleeping()
    sleep_mode.on_presence_poll(False)
    assert sleep_mode.is_sleeping()


def test_present_poll_resets_absent_streak():
    _reset()
    sleep_mode.on_presence_poll(False)
    sleep_mode.on_presence_poll(True)
    assert sleep_mode._streak == 0


def test_present_streak_reaches_debounce_exits_sleep_mode():
    """Mirrors test_streak_reaches_debounce_enters_sleep_mode, for the
    return trip: a single present poll must not immediately exit sleep
    mode, only PRESENCE_PRESENT_DEBOUNCE consecutive ones."""
    _reset()
    _enter_sleep()

    for _ in range(sleep_mode.PRESENCE_PRESENT_DEBOUNCE - 1):
        sleep_mode.on_presence_poll(True)
        assert sleep_mode.is_sleeping()  # not enough consecutive hits yet
    sleep_mode.on_presence_poll(True)
    assert not sleep_mode.is_sleeping()
    assert sleep_mode._present_streak == sleep_mode.PRESENCE_PRESENT_DEBOUNCE


def test_absent_poll_resets_present_streak():
    """A present poll that doesn't repeat must not carry over — one
    stray absent poll in between should reset the present-streak back
    to zero, same shape as the absent-streak's own reset on present."""
    _reset()
    _enter_sleep()

    sleep_mode.on_presence_poll(True)
    assert sleep_mode._present_streak == 1
    sleep_mode.on_presence_poll(False)
    assert sleep_mode._present_streak == 0
    assert sleep_mode.is_sleeping()  # never actually exited


def test_wake_force_exits_regardless_of_streak():
    _reset()
    _enter_sleep()

    sleep_mode.wake("hotkey")
    assert not sleep_mode.is_sleeping()
    assert sleep_mode._streak == 0
    assert sleep_mode._present_streak == 0


if __name__ == "__main__":
    test_streak_reaches_debounce_enters_sleep_mode()
    test_present_poll_resets_absent_streak()
    test_present_streak_reaches_debounce_exits_sleep_mode()
    test_absent_poll_resets_present_streak()
    test_wake_force_exits_regardless_of_streak()
    print("ok")
