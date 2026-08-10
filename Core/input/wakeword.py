# Core/input/wakeword.py
#
# Always-on "Hey FRED" detection, running ALONGSIDE HoldHotkey
# (Core/input/hotkey.py) — not replacing it, decided 2026-08-09. Two
# genuinely different problems from hold-to-talk, not just a different
# trigger:
#
#   1. No keyup to mark the end of an utterance. hold-to-talk's whole
#      design (see stt_whisper.py's module docstring) leans on the key
#      release AS the endpoint. Wake-word has nothing equivalent, so
#      end-of-utterance has to be guessed from silence — see
#      watch_for_silence() below.
#
#   2. Continuous mic capture competing for the same input device
#      WhisperSTT opens for a real turn. This listener owns its own
#      InputStream and must release it (pause()) before a turn's own
#      stream opens, then reopen (resume()) once the turn ends — the
#      caller is responsible for calling these at the same points
#      touch()/screen_watcher already coordinate around turn
#      boundaries.
#
# The model itself (Core/models/wakeword/hey_fred.onnx) is trained by
# Core/input/wakeword_train.py — see that script for how, and to
# retrain with better negative data.

import threading
import time

import numpy as np
import sounddevice as sd

from config.settings import WAKEWORD_MODEL_PATH, WAKEWORD_THRESHOLD
from utils import event_log

CHUNK = 1280  # 80ms @ 16kHz — openwakeword's required frame size
SR = 16000

# How long a continuous near-silence after real speech ends the
# utterance — same shape as any push-to-talk assistant's endpointing,
# just guessed from RMS rather than a keyup. Generous on purpose:
# cutting off a trailing word is a worse failure than half a second of
# extra silence at the end.
SILENCE_TIMEOUT_SECONDS = 1.2
MAX_UTTERANCE_SECONDS = 15.0  # safety cap if the user never speaks at all
SPEECH_RMS_FLOOR = 0.02

# Refuse a second trigger this soon after the last one — the model
# re-scores every 80ms, and a single utterance of "hey fred" spans
# several chunks, any of which could independently cross the threshold.
_REFIRE_COOLDOWN_SECONDS = 2.0


class WakewordListener:
    """
    Owns its own continuous InputStream, separate from WhisperSTT's
    turn-scoped one. on_wake fires once per detection (same contract as
    HoldHotkey.on_press) — the caller starts STT recording from there,
    same as it would from a hotkey press.
    """

    def __init__(self, on_wake=None, model_path=None, threshold=None):
        self.on_wake = on_wake
        self._model_path = str(model_path or WAKEWORD_MODEL_PATH)
        self._threshold = WAKEWORD_THRESHOLD if threshold is None else threshold

        self._oww = None
        self._stream = None
        self._stream_lock = threading.Lock()
        self._last_fire = 0.0

    def _ensure_model(self):
        if self._oww is not None:
            return
        from openwakeword.model import Model
        self._oww = Model(wakeword_models=[self._model_path], inference_framework="onnx")

    def _callback(self, indata, frames, time_info, status):
        if status:
            event_log.log("wakeword", note=f"input status: {status}")

        block = indata[:, 0]
        int16_block = np.clip(block * 32767, -32768, 32767).astype(np.int16)

        try:
            scores = self._oww.predict(int16_block)
        except Exception as e:
            event_log.log_error("wakeword:predict", e)
            return

        score = next(iter(scores.values()), 0.0) if scores else 0.0
        now = time.monotonic()
        if score > self._threshold and now - self._last_fire > _REFIRE_COOLDOWN_SECONDS:
            self._last_fire = now
            self._oww.reset()
            event_log.log("wakeword", note="fired", score=round(float(score), 3))
            if self.on_wake:
                # Dispatched, not called directly: on_wake is expected to
                # call pause() on THIS listener (to free the mic for a
                # real STT stream), and stopping a sounddevice stream
                # from inside its own callback is unsafe — same reason
                # hotkey.py's callbacks only ever flip state and hand off
                # to another thread, never do real work inline.
                threading.Thread(target=self._fire_wake, daemon=True).start()

    def _fire_wake(self):
        try:
            self.on_wake()
        except Exception as e:
            event_log.log_error("wakeword:on_wake", e)

    def resume(self):
        """Start (or restart) continuous listening. Safe to call repeatedly —
        a no-op while already running."""
        with self._stream_lock:
            if self._stream is not None:
                return
            self._ensure_model()
            from audio import device_info
            stream = sd.InputStream(
                samplerate=SR, channels=1, dtype="float32",
                blocksize=CHUNK, callback=self._callback,
                extra_settings=device_info.input_extra_settings(),
            )
            try:
                stream.start()
            except Exception as e:
                # Must not leave self._stream pointing at a constructed-
                # but-never-started stream: resume()'s own guard above
                # ("if self._stream is not None: return") would then
                # silently no-op every future call forever, since it
                # only checks the reference exists, not that it's
                # actually running — the listener would look paused
                # permanently after one transient failure, with nothing
                # visibly wrong. self._stream stays None here so the
                # next resume() call gets a real retry instead.
                event_log.log_error("wakeword:resume", e)
                return
            self._stream = stream
            event_log.log("wakeword", note="resumed")

    def pause(self):
        """Release the mic device — call before a real turn's own STT
        stream opens, so the two never fight over the same input device."""
        with self._stream_lock:
            if self._stream is None:
                return
            stream, self._stream = self._stream, None
            try:
                stream.stop()
                stream.close()
                event_log.log("wakeword", note="paused")
            except Exception as e:
                event_log.log_error("wakeword:pause", e)


def watch_for_silence(stt, on_timeout, stop_flag):
    """
    Runs on its own thread once a wake-triggered turn starts recording,
    calling on_timeout() once silence ends the utterance (or the
    MAX_UTTERANCE_SECONDS safety cap is hit regardless). Polls
    stt.level, already computed every audio callback by WhisperSTT — no
    separate VAD model needed for this.

    stop_flag: a threading.Event the caller sets to cancel this watch
    early (e.g. the turn already ended some other way, like a hotkey
    press interrupting it).
    """
    start = time.monotonic()
    heard_speech = False
    last_loud = start
    while not stop_flag.is_set():
        now = time.monotonic()
        if now - start > MAX_UTTERANCE_SECONDS:
            on_timeout()
            return
        if stt.level > SPEECH_RMS_FLOOR:
            heard_speech = True
            last_loud = now
        elif heard_speech and now - last_loud > SILENCE_TIMEOUT_SECONDS:
            on_timeout()
            return
        time.sleep(0.05)
