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

import collections
import threading
import time

import numpy as np
import sounddevice as sd

from config.settings import WAKEWORD_MODEL_PATH, WAKEWORD_THRESHOLD
from input import wakeword_log

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

# Adaptive gain — added 2026-08-10 after Vatsal measured Windows' own
# mic level meter directly: ~80% peak close to the mic, ~20% at normal
# speaking distance. Deliberately NOT a fixed/maximum multiplier applied
# regardless of level — a blind boost is actively dangerous, not just
# unhelpful: tested the same day, pushed far enough to clip, score
# collapsed from 0.998 to 0.013. The request was for maximum boost in
# any condition; the way to actually get that without it destroying the
# signal on a loud moment is to target a safe peak level and let the
# ceiling below be generous, not to apply a fixed gain irrespective of
# how loud the input already is. At Vatsal's own measured range (80%
# down to 20%) the needed gain is only 0.87x-3.5x either way — this
# ceiling only starts mattering for moments quieter than what he
# measured. Smoothed across chunks (see _agc_gain below) rather than
# jumping per 80ms block — openwakeword's own feature window spans
# ~760ms (76 stacked ~10ms frames), and an abrupt level jump inside
# that window would look like an artifact, not natural speech.
_AGC_TARGET_PEAK = 0.7          # fraction of full scale, leaves clipping headroom
_AGC_MAX_GAIN = 100.0            # ~40dB ceiling — generous for even faint moments
_AGC_MIN_PEAK_TO_BOOST = 0.01   # near-silence: hold gain rather than amplifying noise
_AGC_SMOOTHING = 0.3             # per-chunk convergence toward the target gain

# Rolling pre-trigger buffer for wakeword_capture.py's live dataset —
# the "hey fred" utterance itself already happened by the time a chunk
# crosses threshold, and nothing kept it anywhere; this is what lets a
# fire (or a cancelled/silent one) be saved as actual audio instead of
# just the numeric score already in wakeword_log.jsonl. 2.5s comfortably
# covers a spoken "hey fred" at any reasonable pace. Raw (pre-AGC) on
# purpose: AGC gain swings per-chunk and applying it here would bake an
# inconsistent, runtime-only artifact into training data — every other
# real recording this pipeline uses (room_recording/, real_positive/)
# is raw mic capture too.
_PRETRIGGER_SECONDS = 2.5
_PRETRIGGER_CHUNKS = int(_PRETRIGGER_SECONDS * SR / CHUNK)


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
        self._agc_gain = 1.0  # persists across callbacks — see _AGC_SMOOTHING

        # See _PRETRIGGER_SECONDS. last_trigger_audio/last_fired_score
        # are snapshotted at fire time for wakeword_capture.py — read
        # by the caller (pill_app.py's _on_wake_detected) right after
        # on_wake() is dispatched, before the next fire can overwrite
        # them.
        self._pretrigger = collections.deque(maxlen=_PRETRIGGER_CHUNKS)
        self.last_trigger_audio = None
        self.last_fired_score = 0.0
        self.last_fired_gain = 1.0

    def _ensure_model(self):
        if self._oww is not None:
            return
        from openwakeword.model import Model
        self._oww = Model(wakeword_models=[self._model_path], inference_framework="onnx")

    def _callback(self, indata, frames, time_info, status):
        try:
            self._process_chunk(indata, status)
        except Exception as e:
            # Last-resort catch-all around the whole callback. Before
            # this, only predict() and log_score() were individually
            # guarded — an exception in the AGC math above them (or
            # anywhere else in _process_chunk) was completely
            # unprotected. Confirmed live 2026-08-10 both failure shapes
            # actually happen: an unguarded TypeError surfaced as a
            # visible error dialog (the json numpy-bool bug), and
            # separately a live attempt produced ZERO log output at
            # all — no score, no error, nothing — consistent with an
            # exception hitting exactly this unprotected AGC path and
            # dying with no trace. Neither is acceptable for something
            # being actively debugged; this must be the one thing that
            # can never itself go dark.
            try:
                wakeword_log.log_event("callback_error", message=str(e))
            except Exception:
                pass
            print(f"[wakeword] callback error: {e}")

    def _process_chunk(self, indata, status):
        if status:
            wakeword_log.log_event("input_status", note=str(status))

        block = indata[:, 0]
        self._pretrigger.append(block.copy())  # raw, pre-AGC — see _PRETRIGGER_SECONDS

        peak = float(np.max(np.abs(block)))
        if peak > _AGC_MIN_PEAK_TO_BOOST:
            desired_gain = min(_AGC_TARGET_PEAK / peak, _AGC_MAX_GAIN)
            if desired_gain < self._agc_gain:
                # Snap down immediately, don't smooth: this chunk is
                # louder than the running gain expects (e.g. the actual
                # "hey fred" syllable arriving right after a quiet
                # stretch had ramped gain up). Smoothing the decrease
                # too, like the increase below, let leftover high gain
                # overshoot onto a genuinely loud chunk — confirmed by
                # test 2026-08-10: measured post-gain peaks hitting the
                # clip ceiling despite the cap, because gain caught up
                # one chunk too late. Gain reduction has to be instant;
                # only gain increase (quiet -> louder-but-still-safe)
                # is safe to ramp slowly.
                self._agc_gain = desired_gain
            else:
                self._agc_gain += (desired_gain - self._agc_gain) * _AGC_SMOOTHING
        # else: near-silence, hold the current gain rather than reacting to it
        block = np.clip(block * self._agc_gain, -1.0, 1.0)

        int16_block = np.clip(block * 32767, -32768, 32767).astype(np.int16)

        try:
            scores = self._oww.predict(int16_block)
        except Exception as e:
            wakeword_log.log_event("predict_error", message=str(e))
            return

        score = next(iter(scores.values()), 0.0) if scores else 0.0
        now = time.monotonic()
        # bool(...) is load-bearing: openwakeword's scores are numpy
        # floats, so `score > threshold` is a numpy.bool_, not a native
        # bool — and json can't serialize that. Confirmed live
        # 2026-08-10: this crashed INSIDE the PortAudio callback thread
        # (visible error dialog, "TypeError: Object of type bool is not
        # JSON serializable") on every near-miss score specifically —
        # exactly the case the logger exists to capture, and exactly
        # the case that hits this code path most (a real detection is
        # rare; a near-miss below threshold is common once someone's
        # actually testing it).
        fired = bool(score > self._threshold and now - self._last_fire > _REFIRE_COOLDOWN_SECONDS)
        try:
            wakeword_log.log_score(score, self._agc_gain, self._threshold, fired)
        except Exception as e:
            # Logging must never be able to take down the actual
            # detection path — same reasoning as the predict() guard
            # above, belt-and-suspenders on top of the bool() fix.
            print(f"[wakeword] log_score failed: {e}")
        if fired:
            self._last_fire = now
            self._oww.reset()
            self.last_trigger_audio = np.concatenate(list(self._pretrigger)).astype(np.float32)
            self.last_fired_score = float(score)
            self.last_fired_gain = float(self._agc_gain)
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
            wakeword_log.log_event("on_wake_error", message=str(e))

    def resume(self):
        """Start (or restart) continuous listening. Safe to call repeatedly —
        a no-op while already running."""
        with self._stream_lock:
            if self._stream is not None:
                return
            self._ensure_model()
            from audio import device_info

            def _open_stream():
                stream = sd.InputStream(
                    samplerate=SR, channels=1, dtype="float32",
                    blocksize=CHUNK, callback=self._callback,
                    extra_settings=device_info.input_extra_settings(),
                )
                stream.start()
                return stream

            try:
                self._stream = _open_stream()
                wakeword_log.log_event("resumed")
                return
            except Exception as e:
                wakeword_log.log_event("resume_failed", message=str(e))

            # Self-heal, immediately, in the same call — not just log and
            # wait for whoever calls resume() next. Re-resolve the mic by
            # NAME first: PortAudio indices are per-process, so a device
            # appearing or vanishing (a phone plugged in over USB, a
            # Bluetooth headset connecting) shifts them underneath a
            # long-running FRED and sd.default.device can end up pointing
            # at an index that no longer opens. Confirmed 2026-08-15: a
            # USB plug/unplug left every retry failing on the same dead
            # index with PaErrorCode -9999, and FRED sat deaf for an hour
            # with only a log line to say so — that hour is exactly what
            # this retry closes. apply_saved_devices() looks the
            # remembered name up among the devices present RIGHT NOW,
            # which is what a retry after a topology change needs.
            try:
                device_info.apply_saved_devices()
            except Exception as reselect_error:
                wakeword_log.log_event(
                    "reselect_failed", message=str(reselect_error)
                )
                # self._stream stays None so a future resume() call still
                # gets a real retry instead of silently no-op'ing forever
                # (see the guard at the top of this method).
                return

            # Bounded to exactly one retry, not a loop: a device that's
            # genuinely gone (unplugged, driver crashed) must fail fast
            # and leave a clear log line, not spin.
            try:
                self._stream = _open_stream()
                wakeword_log.log_event("resumed_after_reselect")
            except Exception as e:
                wakeword_log.log_event("resume_retry_failed", message=str(e))

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
                wakeword_log.log_event("paused")
            except Exception as e:
                wakeword_log.log_event("pause_failed", message=str(e))


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
