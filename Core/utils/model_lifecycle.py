# Core/utils/model_lifecycle.py
#
# Frees model VRAM when FRED has been idle, and loads it back before it's
# needed rather than when it's needed.
#
# FRED now runs from log-on to shut-down, so the resident cost matters:
# the LLM holds ~4.8GB and Whisper ~1.3GB, about 37% of a 16GB card
# between them. Both runtimes genuinely release (measured: 4566 and
# 1072 MiB reclaimed), so this is real rather than theatre.
#
# Two ideas make it not-a-latency-regression:
#
#   1. A single watchdog thread, not a timer per turn. One `last_used`
#      stamp, one 30s tick, no cancel/reschedule churn and no way to leak
#      timers. If a turn is in flight the tick simply skips and retries.
#
#   2. Reload starts on the hotkey PRESS, concurrently with the user
#      speaking, not on demand after they finish. Audio capture needs no
#      model, so recording begins instantly regardless, and only an
#      utterance shorter than the load can outrun it.
#
# Whisper gets a longer grace period than the LLM because it is both more
# expensive to reload (2.9s vs 1.9s) and needed sooner after a keypress.

import threading
import time

from config.settings import (
    LLM_IDLE_UNLOAD_SECONDS,
    WHISPER_UNLOAD_AFTER_LLM_SECONDS,
    KOKORO_UNLOAD_AFTER_WHISPER_SECONDS,
    MODEL_WATCHDOG_TICK_SECONDS,
)


class ModelLifecycle:
    """
    Owns the idle-unload policy. `busy` is a callable returning True while
    a turn is running, so an unload can never race generation.
    """

    def __init__(self, llm=None, stt=None, tts=None, busy=None):
        self.llm = llm
        self.stt = stt
        self.tts = tts
        self.busy = busy or (lambda: False)

        self._last_used = time.monotonic()
        self._llm_unloaded_at = None
        self._stt_unloaded_at = None
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    # =========================================================
    # SIGNALS FROM THE APP
    # =========================================================

    def touch(self):
        """Mark FRED as just-used. Called when a turn ends."""
        with self._lock:
            self._last_used = time.monotonic()
            self._llm_unloaded_at = None
            self._stt_unloaded_at = None

    def preload(self):
        """
        Bring back whatever was unloaded, off-thread.

        Called on hotkey-down. Returns immediately — the caller is about to
        start recording and must not be blocked by a model load.
        """
        self.touch()

        need_llm = self.llm is not None and not self.llm.is_loaded()
        need_stt = self.stt is not None and not self.stt.is_loaded()
        need_tts = self.tts is not None and not self.tts.is_loaded()

        if not (need_llm or need_stt or need_tts):
            return

        def run():
            # Whisper first: it's needed at key-up, the LLM only after
            # transcription, so this ordering buys the most slack. Kokoro
            # alongside the LLM — a real reply needs both roughly
            # together, and Kokoro's load is comparatively cheap (CPU
            # ONNX session, not a multi-GB CUDA context).
            if need_stt:
                self.stt.ensure_loaded()
            if need_llm:
                self.llm.ensure_loaded()
            if need_tts:
                self.tts.ensure_loaded()

        threading.Thread(target=run, daemon=True).start()

    # =========================================================
    # WATCHDOG
    # =========================================================

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            time.sleep(MODEL_WATCHDOG_TICK_SECONDS)
            if not self._running:
                return
            try:
                self._tick()
            except Exception as e:
                print(f"[lifecycle] tick failed: {e}")

    def _tick(self):
        if self.busy():
            return  # mid-turn; try again next tick

        with self._lock:
            idle = time.monotonic() - self._last_used
            llm_gone_at = self._llm_unloaded_at
            stt_gone_at = self._stt_unloaded_at

        if (
            self.llm is not None
            and self.llm.is_loaded()
            and idle >= LLM_IDLE_UNLOAD_SECONDS
        ):
            if self.llm.unload():
                with self._lock:
                    self._llm_unloaded_at = time.monotonic()
                print(f"[lifecycle] LLM unloaded after {idle / 60:.0f} min idle")
            return

        # Whisper only once the LLM has already been gone a while, so a
        # brief lull never costs the expensive reload.
        if (
            self.stt is not None
            and self.stt.is_loaded()
            and self.llm is not None
            and not self.llm.is_loaded()
            and llm_gone_at is not None
            and (time.monotonic() - llm_gone_at) >= WHISPER_UNLOAD_AFTER_LLM_SECONDS
        ):
            if self.stt.unload():
                with self._lock:
                    self._stt_unloaded_at = time.monotonic()
                print(f"[lifecycle] Whisper unloaded after {idle / 60:.0f} min idle")
            return

        # Kokoro last, once Whisper's ALSO been gone a while. Low-value
        # relative to the two above (RAM, not VRAM — see
        # KOKORO_UNLOAD_AFTER_WHISPER_SECONDS in settings.py) but kept in
        # the same waterfall for consistency rather than a special case.
        if (
            self.tts is not None
            and self.tts.is_loaded()
            and self.stt is not None
            and not self.stt.is_loaded()
            and stt_gone_at is not None
            and (time.monotonic() - stt_gone_at) >= KOKORO_UNLOAD_AFTER_WHISPER_SECONDS
        ):
            if self.tts.unload():
                print(
                    "[lifecycle] Kokoro unloaded after "
                    f"{idle / 60:.0f} min idle — all models released"
                )

    def status(self) -> str:
        with self._lock:
            idle = time.monotonic() - self._last_used
        llm = "loaded" if (self.llm and self.llm.is_loaded()) else "unloaded"
        stt = "loaded" if (self.stt and self.stt.is_loaded()) else "unloaded"
        tts = "loaded" if (self.tts and self.tts.is_loaded()) else "unloaded"
        return f"idle {idle / 60:.1f} min | LLM {llm} | Whisper {stt} | Kokoro {tts}"
