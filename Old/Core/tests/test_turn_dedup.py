# Rapid repeat hotkey presses used to genuinely queue: _turn_lock only
# ever guaranteed two turns couldn't run AT THE SAME TIME, not that a
# backlog of them wouldn't each still run in full, one after another,
# long after the user had moved on. _turn_seq is the fix — a turn
# checks, the instant it's actually about to run, whether it's still
# the latest one issued, and discards itself silently if not.
#
# PillApp.__init__ needs real STT/TTS/hotkey hardware, so these
# construct a bare instance via __new__ and set only what _run_turn
# actually touches — same pattern used for MemoryManager's tests.

import threading

from ui.pill_app import PillApp


def _bare_app():
    app = PillApp.__new__(PillApp)
    app._turn_lock = threading.Lock()
    app._cancel = threading.Event()
    app._turn_seq = 0
    app._ran = []  # records which sequence numbers actually executed
    app._turn_body = lambda text=None: app._ran.append("body-ran")
    return app


def test_only_the_latest_of_several_stale_presses_runs():
    app = _bare_app()

    # Three presses queued up behind each other, as if fired in rapid
    # succession while a previous turn was still occupying the lock.
    app._turn_seq = 3

    app._run_turn(1)  # stale — a newer press (3) already superseded it
    app._run_turn(2)  # also stale
    app._run_turn(3)  # the actual latest — this one should run

    assert app._ran == ["body-ran"]


def test_a_single_press_with_no_competition_runs_normally():
    app = _bare_app()
    app._turn_seq = 1

    app._run_turn(1)

    assert app._ran == ["body-ran"]


def test_stale_turn_does_not_touch_cancel_state():
    app = _bare_app()
    app._turn_seq = 2
    app._cancel.set()  # simulate: the real (current) turn set this

    app._run_turn(1)  # stale — must not clear _cancel, that's not its state to touch

    assert app._cancel.is_set()


def test_the_winning_turn_does_clear_cancel_state():
    app = _bare_app()
    app._turn_seq = 1
    app._cancel.set()

    app._run_turn(1)

    assert not app._cancel.is_set()


def test_exception_in_turn_body_is_caught_and_logged_not_raised():
    app = _bare_app()
    app._turn_seq = 1
    app._turn_body = lambda text=None: (_ for _ in ()).throw(RuntimeError("boom"))
    app._to_idle_and_hide = lambda: app._ran.append("recovered")

    app._run_turn(1)  # must not raise

    assert app._ran == ["recovered"]
