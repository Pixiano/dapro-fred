# Core/audio/stt_whisper.py
#
# faster-whisper STT for GUI mode's hold-to-talk.
#
# Hold-to-talk changes what the right STT engine is. Vosk (audio/stt.py,
# still used by the CLI) streams partial results and guesses where an
# utterance ends from VAD silence timeouts. Here the key release *is* the
# endpoint, so there is nothing to guess — we record the whole held
# span and transcribe it in one shot. That is exactly the input Whisper
# wants, and it's more accurate than Vosk on the same audio.
#
# The trade being made knowingly: Whisper has no streaming mode, so
# there are no partial results and no live word-by-word display. The
# pill shows the finished transcript after release instead.
#
# CTranslate2 (faster-whisper's runtime) does its own CUDA/cuDNN
# discovery and does not use torch at all — so a CPU-only torch install
# in this venv says nothing about whether this runs on the GPU.

import threading
import time

import numpy as np
import sounddevice as sd

from config.settings import (
    WHISPER_MODEL,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_LANGUAGE,
    MAX_UTTERANCE_SECONDS,
    STT_SAMPLE_RATE,
)

BLOCK_FRAMES = 1024


class WhisperSTT:
    """
    Records while a key is held, transcribes on release.

    Usage is strictly paired: start_recording() then stop_and_transcribe().
    `level` exposes the current input RMS (0..1) for the pill's listening
    waveform, updated from the audio callback.
    """

    def __init__(self, model_name=None, device=None, compute_type=None):
        self.samplerate = STT_SAMPLE_RATE
        self.language = WHISPER_LANGUAGE

        self._blocks = []
        self._stream = None
        self._lock = threading.Lock()
        # Separate lock for the PortAudio stream's lifetime. The stream is
        # opened on the recording thread but closed from the turn thread
        # (and possibly the cancel button's thread too), and letting an
        # open race a close means PortAudio can be handed a half-freed
        # stream — which surfaces as a bare access violation, not an
        # exception. Reentrant because cancel_recording closes via a path
        # that may already hold it.
        self._stream_lock = threading.RLock()
        self._recording = False

        self.level = 0.0
        self._max_frames = int(MAX_UTTERANCE_SECONDS * self.samplerate)

        from faster_whisper import WhisperModel

        name = model_name or WHISPER_MODEL
        device = device or WHISPER_DEVICE
        compute_type = compute_type or WHISPER_COMPUTE_TYPE

        t0 = time.time()
        self.model = WhisperModel(name, device=device, compute_type=compute_type)

        # Read the *resolved* device off the CTranslate2 model rather than
        # echoing the requested one — "auto" silently falls back to CPU,
        # and that's the difference between a 0.25s and a 4s transcript.
        ct2 = self.model.model
        self.device = getattr(ct2, "device", device)
        self.compute_type = getattr(ct2, "compute_type", compute_type)
        load_s = time.time() - t0

        self._warm_up()
        print(
            f"[WhisperSTT] '{name}' on {self.device}/{self.compute_type} — "
            f"load {load_s:.1f}s, warm-up {time.time() - t0 - load_s:.1f}s"
        )

    def _warm_up(self):
        """
        Decode a throwaway buffer at startup.

        The first CUDA transcription in a process pays for context
        creation and kernel selection — measured at ~14s here, against
        ~0.25s once warm. Without this, that entire cost lands on the
        user's first utterance and reads as FRED being broken.
        """
        try:
            silence = np.zeros(self.samplerate, dtype=np.float32)
            segments, _ = self.model.transcribe(
                silence, language=self.language, beam_size=1
            )
            list(segments)  # generator — must be drained to actually run
        except Exception as e:
            print(f"[WhisperSTT] warm-up skipped: {e}")

    # =========================================================
    # RECORDING
    # =========================================================

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[WhisperSTT] input status: {status}")

        block = indata[:, 0].copy()

        # RMS scaled into a usable 0..1 display range. Speech at normal
        # volume sits well below full scale, so a flat RMS would barely
        # move the waveform — the multiplier is display gain, not
        # normalisation, and is clamped rather than fitted.
        rms = float(np.sqrt(np.mean(block * block)))
        self.level = min(1.0, rms * 12.0)

        with self._lock:
            if not self._recording:
                return
            total = sum(len(b) for b in self._blocks)
            if total < self._max_frames:
                self._blocks.append(block)

    def start_recording(self):
        with self._stream_lock:
            with self._lock:
                if self._recording:
                    return
                self._blocks = []
                self._recording = True

            # Close any stream still lingering from a previous turn before
            # opening a new one — pressing the hotkey again mid-answer is
            # an ordinary interrupt, so this path is routine, not an edge
            # case, and leaking a stream per interrupt would exhaust the
            # device.
            self._close_stream_locked()

            self.level = 0.0
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                blocksize=BLOCK_FRAMES,
                callback=self._callback,
            )
            self._stream.start()

    def _close_stream_locked(self):
        """Caller must hold _stream_lock."""
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                # stop() blocks until the PortAudio callback has finished,
                # so it must precede close() — closing under a running
                # callback is the use-after-free case.
                stream.stop()
                stream.close()
            except Exception as e:
                print(f"[WhisperSTT] stream close failed: {e}")
        self.level = 0.0

    def _close_stream(self):
        with self._stream_lock:
            self._close_stream_locked()

    def cancel_recording(self):
        """Abandon the current recording without transcribing."""
        with self._lock:
            self._recording = False
            self._blocks = []
        self._close_stream()

    # =========================================================
    # TRANSCRIPTION
    # =========================================================

    def stop_and_transcribe(self, min_seconds: float = 0.25) -> str:
        with self._lock:
            self._recording = False
            blocks = self._blocks
            self._blocks = []

        self._close_stream()

        if not blocks:
            return ""

        audio = np.concatenate(blocks).astype(np.float32)

        # Guards against an accidental tap producing a spurious
        # transcription — Whisper will happily hallucinate a phrase out
        # of a few milliseconds of room noise.
        if len(audio) < min_seconds * self.samplerate:
            return ""

        try:
            segments, _info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=1,          # greedy: this is a latency path
                vad_filter=True,      # drops leading/trailing silence
            )
            return " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as e:
            print(f"[WhisperSTT] transcribe failed: {e}")
            return ""
