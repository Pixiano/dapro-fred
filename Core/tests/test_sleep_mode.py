# Core/tests/test_sleep_mode.py
#
# Pure unit tests against the streak/state machine in
# orchestrator/sleep_mode.py — no camera/hotkey I/O, just booleans in.

from orchestrator import sleep_mode


def _reset():
    sleep_mode._streak = 0
    sleep_mode._sleeping = False


def test_streak_reaches_debounce_enters_sleep_mode():
    _reset()
    for _ in range(sleep_mode.PRESENCE_ABSENT_DEBOUNCE - 1):
        sleep_mode.on_presence_poll(False)
        assert not sleep_mode.is_sleeping()
    sleep_mode.on_presence_poll(False)
    assert sleep_mode.is_sleeping()


def test_present_poll_resets_streak_and_exits_sleep_mode():
    _reset()
    for _ in range(sleep_mode.PRESENCE_ABSENT_DEBOUNCE):
        sleep_mode.on_presence_poll(False)
    assert sleep_mode.is_sleeping()

    sleep_mode.on_presence_poll(True)
    assert not sleep_mode.is_sleeping()
    assert sleep_mode._streak == 0


def test_wake_force_exits_regardless_of_streak():
    _reset()
    for _ in range(sleep_mode.PRESENCE_ABSENT_DEBOUNCE):
        sleep_mode.on_presence_poll(False)
    assert sleep_mode.is_sleeping()

    sleep_mode.wake("hotkey")
    assert not sleep_mode.is_sleeping()
    assert sleep_mode._streak == 0


if __name__ == "__main__":
    test_streak_reaches_debounce_enters_sleep_mode()
    test_present_poll_resets_streak_and_exits_sleep_mode()
    test_wake_force_exits_regardless_of_streak()
    print("ok")
