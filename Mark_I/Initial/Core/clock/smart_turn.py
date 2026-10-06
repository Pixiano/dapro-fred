"""Smart Turn v3.2 (pipecat, BSD-2, 8 M parameters, int8 ONNX, CPU): "has this speaker finished their turn?" from the raw audio, no transcript.

Sockets
  in : SmartTurn(path=None).prob(audio) -> float in [0, 1]; audio = 16 kHz mono int16 or float32 ndarray of the CURRENT utterance (any length; the last 8 s are used,
       shorter audio is zero-padded at the front, exactly like the reference implementation)
  out: probability the turn is complete; >= `turn_prob_threshold` (0.5) means "done talking". Raises SmartTurnError if the model file is missing or fails.
Model file: <Core>/weights/smart_turn/smart-turn-v3.2-cpu.onnx (git-ignored; download: huggingface_hub.hf_hub_download("pipecat-ai/smart-turn-v3", "smart-turn-v3.2-cpu.onnx",
  local_dir="weights/smart_turn")). CPU by design: a turn-detection model is on the CPU allowance, ~12 ms per call measured upstream, measured here in the self-check.
Features: Whisper-style log-mel (80 bins, 8 s -> 80 x 800), computed with faster_whisper's FeatureExtractor (numpy), after zero-mean / unit-variance normalisation of the padded
  waveform, as in the reference code. No `transformers` dependency.
Self-check: python clock/smart_turn.py (shape, range, silence vs speech-like noise, latency; the accuracy gate on spoken sentences is tests/offline_clock.py)
"""
import sys
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

SR, SECONDS = 16000, 8
MODEL = Path(__file__).resolve().parents[1] / "weights" / "smart_turn" / "smart-turn-v3.2-cpu.onnx"


class SmartTurnError(RuntimeError):
    pass


class SmartTurn:
    def __init__(self, path=None):
        self.path = Path(path) if path else MODEL
        self._sess = None
        self._fe = None

    def _load(self):
        if self._sess is None:
            if not self.path.exists():
                raise SmartTurnError(f"model file missing: {self.path}")
            import onnxruntime as ort
            from faster_whisper.feature_extractor import FeatureExtractor
            so = ort.SessionOptions()
            so.inter_op_num_threads, so.intra_op_num_threads = 1, 1
            self._sess = ort.InferenceSession(str(self.path), sess_options=so, providers=["CPUExecutionProvider"])
            self._fe = FeatureExtractor(feature_size=80, sampling_rate=SR, hop_length=160, chunk_length=SECONDS, n_fft=400)

    def prob(self, audio) -> float:
        self._load()
        x = np.asarray(audio)
        if x.dtype == np.int16:
            x = x.astype(np.float32) / 32768.0
        x = x.astype(np.float32).ravel()
        n = SR * SECONDS
        x = x[-n:] if len(x) > n else np.pad(x, (n - len(x), 0))
        x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
        feats = self._fe(x, padding=0)[:, :800].astype(np.float32)[None]
        try:
            p = float(self._sess.run(None, {"input_features": feats})[0].ravel()[0])   # the output named "logits" is already a probability (sigmoid is inside the graph)
        except Exception as ex:
            raise SmartTurnError(f"inference failed: {ex!r}")
        return min(1.0, max(0.0, p))


def _selfcheck():
    if not MODEL.exists():
        print(f"smart_turn SKIPPED: weights missing ({MODEL}); see the download recipe in the docstring / README")
        return
    st = SmartTurn()
    rng = np.random.default_rng(0)
    quiet = (rng.normal(size=SR * 3) * 20).astype(np.int16)
    noisy = (rng.normal(size=SR * 12) * 3000).astype(np.int16)                  # longer than 8 s: truncation path
    p1, p2 = st.prob(quiet), st.prob(noisy)
    assert 0.0 <= p1 <= 1.0 and 0.0 <= p2 <= 1.0, (p1, p2)
    assert st.prob(quiet.astype(np.float32) / 32768) == st.prob(quiet)          # int16 and float input agree
    t = time.perf_counter()
    for _ in range(10):
        st.prob(quiet)
    ms = (time.perf_counter() - t) * 100
    assert ms < 150, f"too slow on this CPU: {ms:.0f} ms per call"
    try:
        SmartTurn("nope.onnx").prob(quiet)
        raise AssertionError("missing model must raise")
    except SmartTurnError:
        pass
    print(f"smart_turn ok  ({ms:.0f} ms per call, silence-like {p1:.2f}, noise {p2:.2f})")


if __name__ == "__main__":
    _selfcheck()
