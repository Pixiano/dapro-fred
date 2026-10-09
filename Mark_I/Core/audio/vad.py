"""Voice activity: per-20 ms speech/non-speech verdicts turned into clean speech-start / speech-end events.

VAD only says "someone is talking"; whether the TURN is over is the interaction clock's call (L1: silence + Smart Turn). So the hangover here is short
(settings.vad_hangover_ms) and the clock adds its own rules on top, using `silence_ms` and the events below.

Sockets
  in : Vad(bus, model=None).attach(mic)    or on_chunk(int16 chunk of 1280, ts) for replay/tests
       model = anything with is_speech(frame: int16 [320]) -> bool; default WebrtcModel (settings.vad_mode). A Silero model plugs in here the same way (L1 open check).
  out: bus events  audio.vad {speaking: True,  ts}                      onset: leaky counter reaches vad_onset_ms of voiced frames (one noisy frame never starts it)
                   audio.vad {speaking: False, ts, speech_ms}           vad_hangover_ms of continuous silence after speech
       properties: speaking, silence_ms (length of the current silence run, 0 while a voiced frame is the latest), last_voice_ts
Frame timestamps advance 20 ms inside a chunk, so replayed recordings produce the same timeline as live audio.
Self-check: python audio/vad.py
"""
import sys
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev

FRAME_MS = 20
FRAME = 320   # samples at 16 kHz


class WebrtcModel:
    def __init__(self, mode: int | None = None):
        import webrtcvad
        self._v = webrtcvad.Vad(config.get().vad_mode if mode is None else mode)

    def is_speech(self, frame: np.ndarray) -> bool:
        return self._v.is_speech(frame.tobytes(), config.get().sample_rate)


class Vad:
    def __init__(self, bus: ev.EventBus | None = None, model=None):
        self.bus = bus or ev.bus
        self._model = model
        self._carry = np.zeros(0, dtype=np.int16)
        self.speaking = False
        self.silence_ms = 0
        self.last_voice_ts = 0.0
        self._lead = 0               # leaky voiced-frame counter before onset
        self._speech_frames = 0
        self._onset_ts = 0.0
        self._sos = self._zi = None

    def _highpass(self, chunk: np.ndarray) -> np.ndarray:
        """Only the detector sees this: mains hum and rumble (50 Hz) read as speech to the GMM after the auto-gain-free night (2026-10-09 soak: 344 false utterances). STT, wake word and voice ID keep the raw audio."""
        hz = config.get().vad_highpass_hz
        if not hz:
            return chunk
        from scipy.signal import butter, sosfilt
        if self._sos is None:
            self._sos = butter(4, hz, "highpass", fs=config.get().sample_rate, output="sos")
            self._zi = np.zeros((self._sos.shape[0], 2))
        out, self._zi = sosfilt(self._sos, chunk.astype(np.float32), zi=self._zi)
        return np.clip(out, -32768, 32767).astype(np.int16)

    def attach(self, mic):
        return mic.subscribe(self.on_chunk)

    def reset(self):
        self._carry = np.zeros(0, dtype=np.int16)
        self.speaking, self.silence_ms, self._lead, self._speech_frames = False, 0, 0, 0

    def on_chunk(self, chunk: np.ndarray, ts: float):
        if self._model is None:
            self._model = WebrtcModel()
        c = config.get()
        onset_frames = max(1, c.vad_onset_ms // FRAME_MS)
        hang_frames = max(1, c.vad_hangover_ms // FRAME_MS)
        chunk = self._highpass(chunk)
        x = np.concatenate([self._carry, chunk]) if len(self._carry) else chunk
        n = len(x) // FRAME
        base = ts - len(self._carry) / c.sample_rate
        for i in range(n):
            t = base + i * FRAME_MS / 1000.0
            voiced = bool(self._model.is_speech(x[i * FRAME:(i + 1) * FRAME]))
            if voiced:
                self.silence_ms, self.last_voice_ts = 0, t
            else:
                self.silence_ms += FRAME_MS
            if not self.speaking:
                self._lead = self._lead + 1 if voiced else max(0, self._lead - 1)
                if self._lead >= onset_frames:
                    self.speaking, self._speech_frames, self._onset_ts = True, self._lead, t - (self._lead - 1) * FRAME_MS / 1000.0
                    self.bus.emit("audio", "vad", speaking=True, ts=round(self._onset_ts, 3))
            else:
                self._speech_frames += 1
                if self.silence_ms >= c.vad_hangover_ms and self.silence_ms // FRAME_MS >= hang_frames:
                    self.speaking, self._lead = False, 0
                    self.bus.emit("audio", "vad", speaking=False, ts=round(t, 3), speech_ms=(self._speech_frames - hang_frames) * FRAME_MS)
        self._carry = x[n * FRAME:]


class _Pattern:
    """Scripted per-frame verdicts; after the script runs out: silence."""

    def __init__(self, bits):
        self.bits = list(bits)

    def is_speech(self, frame):
        return self.bits.pop(0) if self.bits else False


def _selfcheck():
    t = np.arange(8000) / 16000
    peak = lambda hz: float(np.abs(Vad(ev.EventBus())._highpass((np.sin(2 * np.pi * hz * t) * 10000).astype(np.int16))[4000:]).max())
    assert peak(50) < 500 and peak(500) > 9000                     # hum out, speech band in
    b = ev.EventBus()
    got = []
    b.subscribe(lambda e: got.append(dict(e.data)), kinds={"vad"})
    chunk = np.zeros(1280, dtype=np.int16)                # 80 ms = 4 frames of 20 ms
    assert 1280 // FRAME == 4
    # 1 noisy voiced frame in silence: no onset
    v = Vad(b, _Pattern([1] + [0] * 7))
    v.on_chunk(chunk, 0.0), v.on_chunk(chunk, 0.08)
    assert got == [] and not v.speaking
    # voiced 10 frames (200 ms) then silence: onset (needs 5 voiced), end after 10 silent frames (200 ms)
    v = Vad(b, _Pattern([1] * 10 + [0] * 12))
    for i in range(6):
        v.on_chunk(chunk, 100.0 + i * 0.08)               # 24 frames
    starts = [g for g in got if g["speaking"]]
    ends = [g for g in got if not g["speaking"]]
    assert len(starts) == 1 and len(ends) == 1 and not v.speaking
    assert abs(starts[0]["ts"] - 100.0) < 1e-6            # onset is back-dated to the first voiced frame of the run
    assert ends[0]["ts"] > starts[0]["ts"] and ends[0]["speech_ms"] == 200
    # a short gap inside speech (3 frames) does not end it
    got.clear()
    v = Vad(b, _Pattern([1] * 8 + [0] * 3 + [1] * 8 + [0] * 20))
    for i in range(10):
        v.on_chunk(chunk, 200.0 + i * 0.08)
    assert [g["speaking"] for g in got] == [True, False]
    assert v.silence_ms >= 200 and v.last_voice_ts > 200.0
    # frames that straddle chunk boundaries are carried (1000-sample chunks)
    got.clear()
    v = Vad(b, _Pattern([1] * 12 + [0] * 20))
    odd = np.zeros(1000, dtype=np.int16)
    for i in range(10):
        v.on_chunk(odd, 300.0 + i * 0.0625)
    assert [g["speaking"] for g in got] == [True, False]
    # the real model: digital silence is never speech
    got.clear()
    v = Vad(b)
    for i in range(20):
        v.on_chunk(chunk, 400.0 + i * 0.08)
    assert got == [] and v.silence_ms == 20 * 80
    print("vad ok")


if __name__ == "__main__":
    _selfcheck()
