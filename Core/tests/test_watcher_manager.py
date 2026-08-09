# The safety-critical property of ScreenWatcherManager: touch() must
# kill a running watcher immediately and unconditionally, since it's
# called from the hotkey path right before a real conversation turn
# that needs the GPU. Exercises the manager's methods directly rather
# than waiting on its background idle-timer thread, which is a timing
# concern better verified live than raced against in a unit test.

import time

from vision.watcher_manager import ScreenWatcherManager


def _idle_child():
    """A trivial, real, killable child process target — no model, no
    imports beyond the standard library, just something that sits and
    can be observed alive/dead."""
    time.sleep(30)


def test_touch_kills_a_running_watcher(monkeypatch):
    import multiprocessing

    mgr = ScreenWatcherManager()
    proc = multiprocessing.Process(target=_idle_child, daemon=True)
    proc.start()
    mgr._process = proc

    assert proc.is_alive()
    mgr.touch()

    proc.join(timeout=5)
    assert not proc.is_alive()
    assert mgr._process is None


def test_touch_with_nothing_running_is_a_safe_noop():
    mgr = ScreenWatcherManager()
    mgr.touch()  # must not raise
    assert mgr._process is None


def test_touch_resets_the_idle_clock():
    mgr = ScreenWatcherManager()
    mgr._last_hotkey_activity = 0.0  # simulate "long ago"
    before = mgr._last_hotkey_activity

    mgr.touch()

    assert mgr._last_hotkey_activity > before


def test_capture_now_skips_if_a_watcher_is_already_running():
    """capture_now() must never spawn a second capture process racing
    the one already running — see the module docstring's touch() note
    on why two competing captures can't share the GPU."""
    import multiprocessing

    mgr = ScreenWatcherManager()
    proc = multiprocessing.Process(target=_idle_child, daemon=True)
    proc.start()
    mgr._process = proc

    try:
        assert mgr.capture_now(timeout=1) is False
        assert mgr._process is proc  # untouched, no duplicate spawned
    finally:
        proc.terminate()
        proc.join(timeout=5)


def test_stop_kills_any_running_watcher_on_shutdown():
    import multiprocessing

    mgr = ScreenWatcherManager()
    proc = multiprocessing.Process(target=_idle_child, daemon=True)
    proc.start()
    mgr._process = proc
    mgr._running = True

    mgr.stop()

    proc.join(timeout=5)
    assert not proc.is_alive()
    assert mgr._running is False
