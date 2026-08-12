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

    def capture_now(self, timeout: float = 12.0, force_local: bool = False) -> bool:
        """
        On-demand capture for whats_on_screen() — spawns a one-shot
        capture (screen_watcher.run_once) and waits for a fresh write
        to land, up to timeout seconds.

        Tracked in self._process exactly like the idle-loop watcher,
        so a hotkey press mid-wait kills this the same way touch()
        already kills the background loop — a real conversation turn
        always wins the GPU over an on-demand screen check.

        force_local is passed straight through to run_once() — see its
        docstring. Only meaningful when the caller (whats_on_screen())
        has already freed the main process's VRAM itself.

        Returns whether a fresh capture landed in time. False also
        covers "skipped, something's already running" and "run_once's
        own safety check found a model loaded and silently skipped its
        cycle" — either way the caller just falls back to whatever's
        already cached.
        """
        from vision import screen_context, screen_watcher

        with self._lock:
            if self._process is not None and self._process.is_alive():
                return False
            proc = multiprocessing.Process(
                target=screen_watcher.run_once, args=(force_local,), daemon=True,
            )
            proc.start()
            self._process = proc

        start_wall = time.time()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._process is not proc:
                    return False  # killed by a hotkey press mid-wait
            _, age = screen_context.read()
            if age is not None and (time.time() - age) >= start_wall:
                return True
            time.sleep(0.3)
        return False

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
