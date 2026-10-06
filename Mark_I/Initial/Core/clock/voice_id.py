"""Speaker gate: is this voice Vatsal's? SpeechBrain ECAPA-TDNN (192-d embedding), CPU, ~80 ms per call; cosine similarity against enrolled references.

Sockets
  in : VoiceId(path=None, embedder=None).score(audio) -> float | None;  is_me(audio) -> bool | None;  embed(audio) -> unit 192-d float32 vector
       enroll(audios, k=None)  keep the k (default `voice_refs_cap`, 20) most diverse of these clips' embeddings (replaces the stored set)
       audio = 16 kHz mono int16 or float32 ndarray of ONE speaker's speech
  out: score = best cosine similarity to any stored reference, or None when nothing is enrolled or the clip is shorter than `voice_min_s` (too short to judge: callers
       must treat None as "unknown", never as "not Vatsal" or "Vatsal"). is_me = score >= `voice_threshold`, or None when the score is None.
Stored at <FRED_DATA>/voice_refs.json, outside the repo (biometric: never logged, never in an event, never committed). Embedder default loads <Core>/weights/ecapa (the
  speechbrain/spkrec-ecapa-voxceleb checkpoint files, git-ignored; copied from the old FRED model folder or downloaded by speechbrain), lazily, CPU only.
Self-check: python clock/voice_id.py (fake embedder); real-voice numbers: tests/voice_eval.py
"""
import json
import sys
import threading
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from senses.face_store import diverse

SR = 16000
WEIGHTS = Path(__file__).resolve().parents[1] / "weights" / "ecapa"


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32).ravel()
    return a / max(float(np.linalg.norm(a)), 1e-9)


class EcapaEmbedder:
    def __init__(self):
        self._m, self._lock = None, threading.Lock()

    def __call__(self, wave: np.ndarray) -> np.ndarray:
        import torch
        with self._lock:
            if self._m is None:
                from speechbrain.inference.speaker import EncoderClassifier
                from speechbrain.utils.fetching import LocalStrategy
                if not (WEIGHTS / "embedding_model.ckpt").exists():
                    raise FileNotFoundError(f"ECAPA weights missing in {WEIGHTS}")
                self._m = EncoderClassifier.from_hparams(source=str(WEIGHTS), savedir=str(WEIGHTS), local_strategy=LocalStrategy.COPY, run_opts={"device": "cpu"})
            with torch.no_grad():
                return self._m.encode_batch(torch.from_numpy(wave).unsqueeze(0)).squeeze().numpy()


class VoiceId:
    def __init__(self, path=None, embedder=None):
        self.path = Path(path) if path else config.data_dir() / "voice_refs.json"
        self._embed = embedder or EcapaEmbedder()
        self._refs: np.ndarray | None = None

    def _load(self) -> np.ndarray:
        if self._refs is None:
            try:
                e = json.loads(self.path.read_text(encoding="utf-8"))["e"]
                self._refs = np.stack([_unit(x) for x in e]) if e else np.zeros((0, 192), np.float32)
            except (OSError, ValueError, KeyError):
                self._refs = np.zeros((0, 192), np.float32)
        return self._refs

    @staticmethod
    def _wave(audio) -> np.ndarray:
        x = np.asarray(audio)
        return (x.astype(np.float32) / 32768.0) if x.dtype == np.int16 else x.astype(np.float32).ravel()

    def embed(self, audio) -> np.ndarray:
        return _unit(self._embed(self._wave(audio)))

    def enrolled(self) -> int:
        return len(self._load())

    def enroll(self, audios, k=None) -> int:
        vs = [self.embed(a) for a in audios if len(a) >= config.get().voice_min_s * SR]
        keep = [vs[i] for i in diverse(vs, k or config.get().voice_refs_cap)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": 1, "e": [v.tolist() for v in keep]}), encoding="utf-8")
        tmp.replace(self.path)
        self._refs = np.stack(keep) if keep else np.zeros((0, 192), np.float32)
        return len(keep)

    def score(self, audio) -> float | None:
        refs = self._load()
        if len(refs) == 0 or len(audio) < config.get().voice_min_s * SR:
            return None
        return float((refs @ self.embed(audio)).max())

    def is_me(self, audio) -> bool | None:
        s = self.score(audio)
        return None if s is None else s >= config.get().voice_threshold


def _selfcheck():
    import tempfile
    rng = np.random.default_rng(5)
    centers = {n: rng.normal(size=192) for n in ("me", "other")}

    def fake(wave):                                        # the "voice" is encoded in the waveform's first sample: 1 -> me, 2 -> other
        c = centers["me" if wave[0] > 0 else "other"]
        return c + 0.2 * rng.normal(size=192)

    def clip(who, s=1.5):
        x = np.zeros(int(SR * s), np.float32)
        x[0] = 0.5 if who == "me" else -0.5
        return x

    with tempfile.TemporaryDirectory() as d:
        v = VoiceId(Path(d) / "v.json", fake)
        assert v.score(clip("me")) is None and v.is_me(clip("me")) is None            # nothing enrolled: unknown, not "no"
        assert v.enroll([clip("me") for _ in range(25)], k=20) == 20 and v.enrolled() == 20
        assert v.enroll([clip("me")] + [np.zeros(100, np.float32)], k=5) == 1          # clips too short to embed are ignored
        v.enroll([clip("me") for _ in range(8)])
        assert v.is_me(clip("me")) is True and v.is_me(clip("other")) is False
        assert v.score(np.zeros(1000, np.float32)) is None                              # too short to judge
        assert VoiceId(Path(d) / "v.json", fake).enrolled() == 8                        # persisted
        assert (v.score(clip("me")) or 0) > 0.8 > (v.score(clip("other")) or 1)
    print("voice_id ok")


if __name__ == "__main__":
    _selfcheck()
