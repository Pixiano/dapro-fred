"""WAV loading for replay harnesses and tests: any wav -> mono int16 at the Core sample rate.

Sockets
  in : read16k(path) -> np.ndarray int16 (mono, config sample_rate)
  out: resamples with scipy (polyphase) and mixes channels down; raises ValueError for unreadable audio
Self-check: python audio/wav.py
"""
import sys
from math import gcd
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def to16k(x: np.ndarray, sr: int) -> np.ndarray:
    """float or int array (mono or channels last) at `sr` -> mono int16 at the Core rate."""
    x = np.asarray(x)
    if x.dtype.kind == "i":
        x = x.astype(np.float32) / np.iinfo(x.dtype).max
    x = x.astype(np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    target = config.get().sample_rate
    if sr != target:
        from scipy.signal import resample_poly
        g = gcd(sr, target)
        x = resample_poly(x, target // g, sr // g).astype(np.float32)
    return np.clip(x * 32767, -32768, 32767).astype(np.int16)


def read16k(path) -> np.ndarray:
    import soundfile as sf
    try:
        x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    except Exception as ex:
        raise ValueError(f"cannot read audio {path}: {ex}")
    return to16k(x, sr)


def _selfcheck():
    import tempfile
    import soundfile as sf
    t = np.linspace(0, 1, 8000, endpoint=False)
    stereo = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 440 * t)], axis=1).astype(np.float32) * 0.5
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "a.wav"
        sf.write(f, stereo, 8000)
        y = read16k(f)
        assert y.dtype == np.int16 and abs(len(y) - 16000) <= 2 and 15000 < y.max() < 17500   # 8k stereo -> 16k mono, level kept (0.5 * 32767)
        (Path(d) / "bad.wav").write_bytes(b"nope")
        try:
            read16k(Path(d) / "bad.wav")
            raise AssertionError
        except ValueError:
            pass
    assert to16k(np.array([0, 16384, -32768], dtype=np.int16), 16000).tolist() == [0, 16384, -32768]
    print("wav ok")


if __name__ == "__main__":
    _selfcheck()
