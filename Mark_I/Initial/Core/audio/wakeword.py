"""Wake word: adaptive gain, the frozen openWakeWord model, threshold, cooldown. Listens on the Mic, reports on the bus.

The model (Old/Core/models/wakeword/hey_fred.onnx) is FROZEN: it is read in place from settings.wakeword_model and never copied, retrained or swapped here.
The AGC and the cooldown are ported from the old listener, with the reasons that were measured then: a fixed boost clipped and collapsed the score (0.998 -> 0.013),
so gain targets a safe peak, drops instantly on a loud chunk and only rises slowly; the model re-scores every 80 ms, so a second fire inside the cooldown is refused.

Sockets
  in : WakeDetector(bus, model=None).attach(mic)      subscribes to the Mic's chunks; or call on_chunk(int16 chunk, ts) directly (replay/tests)
       model = anything with predict(int16 chunk) -> float score and reset(); default OwwModel(settings.wakeword_model), loaded on first chunk
  out: bus events  audio.wake {score, gain, threshold}               score above the threshold and outside the cooldown
                   audio.wake_near {score, gain, threshold}          score >= settings.wake_near_miss but not fired (tuning data; the old false trigger was 0.70 vs 0.35)
       last_score, last_gain for the UI. A model failure emits audio.wake_error once per 60 s and the detector keeps running.
Settings: wakeword_threshold, wake_cooldown_s, wake_near_miss, agc_* (all in config.py, none literal here).
Self-check: python audio/wakeword.py     Real-model replay on labelled recordings: tests/replay_audio.py
"""
import sys
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev


class Agc:
    """Gain toward a target peak. Down: immediate. Up: smoothed. Near-silence: hold (never amplify the noise floor)."""

    def __init__(self):
        self.gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        """float32 block in -1..1 -> gained block (clipped)."""
        c = config.get()
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        if peak > c.agc_min_peak:
            want = min(c.agc_target_peak / peak, c.agc_max_gain)
            self.gain = want if want < self.gain else self.gain + (want - self.gain) * c.agc_smoothing
        return np.clip(x * self.gain, -1.0, 1.0)


class OwwModel:
    """openWakeWord ONNX model behind the predict/reset socket."""

    def __init__(self, path: str):
        from openwakeword.model import Model
        self._m = Model(wakeword_models=[str(path)], inference_framework="onnx")

    def predict(self, int16_chunk: np.ndarray) -> float:
        scores = self._m.predict(int16_chunk)
        return float(next(iter(scores.values()), 0.0)) if scores else 0.0

    def reset(self):
        self._m.reset()


class WakeDetector:
    def __init__(self, bus: ev.EventBus | None = None, model=None):
        self.bus = bus or ev.bus
        self._model = model
        self._agc = Agc()
        self._last_fire = -1e9
        self._last_err = -1e9
        self.last_score = 0.0

    @property
    def last_gain(self) -> float:
        return self._agc.gain

    def attach(self, mic):
        return mic.subscribe(self.on_chunk)

    def _ensure(self):
        if self._model is None:
            self._model = OwwModel(config.get().wakeword_model)

    def reset(self):
        """Forget the model's rolling feature window (call after a fire, a mic pause, or between replay clips)."""
        if self._model is not None:
            self._model.reset()
        self._last_fire = -1e9

    def on_chunk(self, chunk: np.ndarray, ts: float):
        c = config.get()
        try:
            self._ensure()
            x = self._agc.process(chunk.astype(np.float32) / 32768.0)
            score = float(self._model.predict(np.clip(x * 32767, -32768, 32767).astype(np.int16)))
        except Exception as ex:
            if ts - self._last_err > 60:
                self._last_err = ts
                self.bus.emit("audio", "wake_error", error=repr(ex))
            return
        self.last_score = score
        info = {"score": round(score, 4), "gain": round(self._agc.gain, 2), "threshold": c.wakeword_threshold}
        if score > c.wakeword_threshold and ts - self._last_fire > c.wake_cooldown_s:
            self._last_fire = ts
            self._model.reset()
            self.bus.emit("audio", "wake", **info)
        elif score >= c.wake_near_miss:
            self.bus.emit("audio", "wake_near", **info)


class _Scripted:
    def __init__(self, scores):
        self.scores, self.resets = list(scores), 0

    def predict(self, chunk):
        return self.scores.pop(0)

    def reset(self):
        self.resets += 1


def _selfcheck():
    a = Agc()
    quiet = np.full(1280, 0.07, dtype=np.float32)
    for _ in range(40):
        out = a.process(quiet)
    assert 0.65 < float(np.max(out)) <= 0.7 + 1e-6 and a.gain > 5                       # quiet speech is raised toward the target peak
    loud = np.full(1280, 0.6, dtype=np.float32)
    out = a.process(loud)
    assert a.gain <= 0.7 / 0.6 + 1e-6 and float(np.max(out)) <= 0.7 + 1e-6              # a loud chunk drops the gain at once: no clipping
    before = a.gain
    a.process(np.full(1280, 0.001, dtype=np.float32))
    assert a.gain == before                                                              # near-silence holds the gain
    b = ev.EventBus()
    got = []
    b.subscribe(lambda e: got.append((e.kind, e.data["score"])), sources={"audio"})
    det = WakeDetector(b, _Scripted([0.0, 0.2, 0.9, 0.95, 0.9, 0.5]))
    chunk = (np.ones(1280) * 800).astype(np.int16)
    for i in range(6):
        det.on_chunk(chunk, 1000.0 + i * 0.08)
    assert got[:2] == [("wake_near", 0.2), ("wake", 0.9)], got
    assert [k for k, _ in got].count("wake") == 1                                        # 0.95 and 0.9 inside the cooldown do not fire again
    det2 = WakeDetector(b, _Scripted([0.9, 0.9]))
    got.clear()
    det2.on_chunk(chunk, 50.0)
    det2.on_chunk(chunk, 50.0 + config.get().wake_cooldown_s + 0.1)
    assert [k for k, _ in got] == ["wake", "wake"]                                       # after the cooldown it fires again
    errs = []
    b.subscribe(lambda e: errs.append(e), kinds={"wake_error"})
    class Boom:
        def predict(self, c): raise RuntimeError("onnx died")
        def reset(self): pass
    d3 = WakeDetector(b, Boom())
    for i in range(5):
        d3.on_chunk(chunk, 10.0 + i * 0.08)
    assert len(errs) == 1                                                                # one error event per 60 s, not 12 per second
    print("wakeword ok")


if __name__ == "__main__":
    _selfcheck()
