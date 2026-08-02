# Core/vision/watcher_manager.py
#
# The main-process side of the screen watcher: owns spawning the child
# process, killing it the instant the hotkey is pressed, and the
# 5-minutes-of-no-hotkey-use timer that decides when to start it again.
# See screen_watcher.py's module docstring for why this is a real OS
# process rather than a thread.

import multiprocessing
import threading
import time

from config.settings import SCREEN_WATCHER_IDLE_MINUTES


class ScreenWatcherManager:
    """
    Two entry points from the app: touch() on every hotkey press/release
    (resets the idle clock and kills anything running), and start()
    called once at app boot to begin the idle-watch thread.

    Deliberately independent of ModelLifecycle — that class manages
    models resident IN THIS process; the watcher's model lives in a
    completely separate process and is governed by wall-clock hotkey
    idle time, not this process's own idle-unload policy.
    """

    def __init__(self):
        self._process = None
        self._last_hotkey_activity = time.monotonic()
        self._lock = threading.Lock()
        self._running = False
        self._watch_thread = None

    # =========================================================
    # SIGNALS FROM THE APP
    # =========================================================

    def touch(self):
        """
        Call on every hotkey press AND release. Resets the idle clock,
        and — the safety-critical part — kills the watcher immediately
        if it's running, so it can never be mid-inference (competing
        for the GPU) at the exact moment a real conversation turn is
        about to start.

        Must return fast: this is called from the same code path as the
        hotkey callback, which Windows silently unhooks if it blocks
        past ~300ms (see input/hotkey.py). terminate() is non-blocking
        — it signals the OS to kill the process and returns immediately,
        it does not wait for the process to actually exit.
        """
        with self._lock:
            self._last_hotkey_activity = time.monotonic()
            self._kill_locked()

    def start(self):
        """Begin the idle-watch thread. Call once at app boot."""
        if self._running:
            return
        self._running = True
        self._watch_thread = threading.Thread(target=self._loop, daemon=True)
        self._watch_thread.start()

    def stop(self):
        """App shutdown — make sure no orphaned child process survives
        the main process exiting."""
        self._running = False
        with self._lock:
            self._kill_locked()

    # =========================================================
    # INTERNAL
    # =========================================================

    def _kill_locked(self):
        """Caller must hold self._lock."""
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
        self._process = None

    def _loop(self):
        while self._running:
            time.sleep(15)  # coarse — this only ever needs minute-scale precision
            with self._lock:
                idle_minutes = (time.monotonic() - self._last_hotkey_activity) / 60
                already_running = self._process is not None and self._process.is_alive()

                if already_running or idle_minutes < SCREEN_WATCHER_IDLE_MINUTES:
                    continue

                self._spawn_locked()

    def _spawn_locked(self):
        """Caller must hold self._lock."""
        from vision import screen_watcher
        proc = multiprocessing.Process(target=screen_watcher.run, daemon=True)
        proc.start()
        self._process = proc
        print(f"[screen_watcher] started after {SCREEN_WATCHER_IDLE_MINUTES} min idle (PID {proc.pid})")
