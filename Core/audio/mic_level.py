# Core/audio/mic_level.py
#
# Lightweight RMS-level monitor feeding the overlay's "listening" ripple
# animation. Independent of the VAD/Vosk capture path. NOTE: if running
# this concurrently with the VAD gate's own InputStream turns out not to
# share the input device cleanly, fold the RMS computation into whichever
# stream is already open instead of running two — see the Phase 16 plan's
# efficiency-budget caveat.

from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from config.settings import STT_SAMPLE_RATE


class MicLevelMonitor:
    def __init__(
        self,
        on_level: Callable[[float], None],
        samplerate: Optional[int] = None,
        block_size: Optional[int] = None,
    ):
        self.on_level = on_level
        self.samplerate = samplerate or STT_SAMPLE_RATE
        self.block_size = block_size or int(self.samplerate * 0.1)  # ~10Hz callbacks
        self._stream: Optional[sd.InputStream] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                blocksize=self.block_size,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[MicLevelMonitor] Failed to start: {e}")
            self._running = False

    def stop(self):
        self._running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[MicLevelMonitor] {status}")
        if not self._running:
            return
        rms = float(np.sqrt(np.mean(np.square(indata))))
        level = min(1.0, rms * 12.0)  # empirical scale factor — retune if too hot/cold
        try:
            self.on_level(level)
        except Exception:
            pass
