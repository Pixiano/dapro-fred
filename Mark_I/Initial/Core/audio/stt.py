"""Speech to text: faster-whisper behind the GPU scheduler, plus the capture buffer that feeds it.

Whisper has no streaming mode, so "windowed" means: decode a bounded buffer of audio on request. The turn clock (L1) decides when: `Capture.snapshot()` +
`Stt.transcribe(audio, final=False)` gives a partial of the audio so far, the same call with final=True ends the turn. No background thread until L1 needs one.

Sockets
  in : Stt(bus=None, model=None).transcribe(audio, prompt=None, final=True, priority=gpu.INTERACTIVE) -> Transcript
         audio = int16 or float32 mono at the Core rate; model = anything with transcribe(float32, **kw) -> (segments, info), default faster-whisper
       Capture(mic).begin(preroll_s=0.5) / snapshot() / end() -> int16 audio     collects from the Mic (plus pre-roll from its ring), capped at max_utterance_s
  out: Transcript(text, language, audio_s, ms, silent); bus event stt.transcript {text, final, audio_s, ms}  (a text event only, never the audio)
Rules: the model runs ONLY through gpu.run (one in-process GPU user at a time) and on settings.whisper_device, default CUDA, never a silent CPU fallback (standing GPU-only rule; CPU only by explicit setting, e.g. for a test); a load/CUDA failure raises
       SttError and emits stt.error. A buffer whose peak stays under stt_min_peak is silence: empty Transcript, no GPU pass (the old "listening for seconds on nothing").
       unload() frees the VRAM (L5 windows). Vocabulary bias: settings.whisper_prompt, or `prompt=` per call.
Self-check: python audio/stt.py (fake model, no GPU).  Real model on recordings: tests/live_stt.py
"""
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
import gpu


class SttError(Exception):
    pass


@dataclass
class Transcript:
    text: str = ""
    language: str = ""
    audio_s: float = 0.0
    ms: int = 0
    silent: bool = False


def _float(audio) -> np.ndarray:
    a = np.asarray(audio)
    return (a.astype(np.float32) / 32768.0) if a.dtype.kind == "i" else a.astype(np.float32)


class Stt:
    def __init__(self, bus: ev.EventBus | None = None, model=None):
        self.bus = bus or ev.bus
        self._model = model

    def _load(self):
        if self._model is None:
            c = config.get()
            try:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(c.whisper_model, device=c.whisper_device, compute_type=c.whisper_compute)
            except Exception as ex:
                self.bus.emit("stt", "error", error=repr(ex))
                raise SttError(f"cannot load {c.whisper_model} on {c.whisper_device}: {ex}")
        return self._model

    def unload(self):
        self._model = None
        import gc
        gc.collect()

    def transcribe(self, audio, prompt: str | None = None, final: bool = True, priority: int = gpu.INTERACTIVE) -> Transcript:
        c = config.get()
        x = _float(audio)
        dur = len(x) / c.sample_rate
        if not len(x) or float(np.max(np.abs(x))) < c.stt_min_peak:
            t = Transcript(audio_s=dur, silent=True)
        else:
            t0 = time.monotonic()

            def work():
                segs, info = self._load().transcribe(x, language=c.whisper_language, beam_size=c.whisper_beam, initial_prompt=prompt or c.whisper_prompt,
                                                     condition_on_previous_text=False, vad_filter=False)
                return " ".join(s.text.strip() for s in segs).strip(), getattr(info, "language", c.whisper_language)
            text, lang = gpu.run("stt", priority, work, timeout=max(30.0, dur * 2 + 20))
            t = Transcript(text, lang, dur, int((time.monotonic() - t0) * 1000))
        self.bus.emit("stt", "transcript", text=t.text, final=final, audio_s=round(dur, 2), ms=t.ms)
        return t


class Capture:
    """Collects mic audio from begin() to end(), prefixed by pre-roll from the Mic's ring (the first word is usually spoken before the trigger lands)."""

    def __init__(self, mic):
        self.mic = mic
        self._chunks: list = []
        self._unsub = None
        self.capped = False

    def begin(self, preroll_s: float = 0.5):
        self.end()
        pre = self.mic.ring(preroll_s) if preroll_s > 0 else np.zeros(0, dtype=np.int16)
        self._chunks, self.capped = ([pre] if len(pre) else []), False
        self._unsub = self.mic.subscribe(self._on)

    def _on(self, chunk, ts):
        if sum(len(c) for c in self._chunks) < config.get().max_utterance_s * config.get().sample_rate:
            self._chunks.append(chunk)
        else:
            self.capped = True

    @property
    def active(self) -> bool:
        return self._unsub is not None

    def snapshot(self) -> np.ndarray:
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.int16)

    def end(self) -> np.ndarray:
        if self._unsub:
            self._unsub()
            self._unsub = None
        out, self._chunks = self.snapshot(), []
        return out


class _Seg:
    def __init__(self, t):
        self.text = t


class _FakeWhisper:
    def __init__(self, text=" hello there "):
        self.text, self.calls = text, []

    def transcribe(self, x, **kw):
        self.calls.append((len(x), kw))
        return iter([_Seg(self.text), _Seg(" again")]), type("I", (), {"language": "en"})()


def _selfcheck():
    from audio.mic import CHUNK, Mic
    b, seen = ev.EventBus(), []
    b.subscribe(seen.append, sources={"stt"})
    fm = _FakeWhisper()
    s = Stt(b, fm)
    loud = (np.sin(np.arange(16000) / 5) * 8000).astype(np.int16)
    t = s.transcribe(loud, final=False)
    assert t.text == "hello there again" and t.language == "en" and abs(t.audio_s - 1.0) < 1e-6 and not t.silent
    assert fm.calls[0][1]["language"] == "en" and fm.calls[0][1]["initial_prompt"] == config.get().whisper_prompt and fm.calls[0][1]["condition_on_previous_text"] is False
    assert s.transcribe(loud, prompt="Fred.").text and fm.calls[1][1]["initial_prompt"] == "Fred."
    n = len(fm.calls)
    q = s.transcribe(np.zeros(16000, dtype=np.int16))
    assert q.silent and q.text == "" and len(fm.calls) == n                      # silence never reaches Whisper
    assert s.transcribe(np.zeros(0, dtype=np.int16)).silent
    assert [(e.kind, e.data["final"]) for e in seen] == [("transcript", False), ("transcript", True), ("transcript", True), ("transcript", True)]
    assert "audio" not in seen[0].data                                            # events carry text only
    f32 = s.transcribe(loud.astype(np.float32) / 32768.0)
    assert f32.text and abs(fm.calls[-1][0] - 16000) == 0
    import faster_whisper                                                         # a CUDA/load failure must raise SttError, never fall back to the CPU
    real, calls = faster_whisper.WhisperModel, []
    faster_whisper.WhisperModel = lambda *a, **k: calls.append(k) or (_ for _ in ()).throw(RuntimeError("no cuda"))
    try:
        Stt(b).transcribe(loud)
        raise AssertionError("expected SttError")
    except SttError:
        assert calls[0]["device"] == "cuda" and any(e.kind == "error" for e in seen)
    finally:
        faster_whisper.WhisperModel = real
    m = Mic(b, device="")
    cap = Capture(m)
    m.feed_array(np.full(CHUNK * 3, 111, dtype=np.int16))                         # earlier audio sits in the ring
    cap.begin(preroll_s=0.16)
    m.feed_array(np.full(CHUNK * 2, 222, dtype=np.int16))
    snap = cap.snapshot()
    assert len(snap) == CHUNK * 4 and snap[0] == 111 and snap[-1] == 222          # 2 chunks pre-roll + 2 new
    m.feed_array(np.full(CHUNK, 333, dtype=np.int16))
    out = cap.end()
    assert len(out) == CHUNK * 5 and not cap.active and len(cap.snapshot()) == 0
    m.feed_array(np.full(CHUNK, 444, dtype=np.int16))
    assert len(cap.snapshot()) == 0                                               # after end() nothing more is collected
    config.get().max_utterance_s = 0.2
    cap.begin(0)
    m.feed_array(np.zeros(CHUNK * 10, dtype=np.int16))
    assert len(cap.end()) <= CHUNK * 4 and cap.capped
    config.reload()
    print("stt ok")


if __name__ == "__main__":
    _selfcheck()
