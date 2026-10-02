"""Checks for the voice + 'answer me now' pieces (no Venus load needed).

1. Kokoro GPU vs CPU: time to synthesise typical first reply chunks.
2. Wake word: FRED's hey_fred.onnx through live_duplex.Addressed fires on a
   Kokoro-spoken "hey fred" and not on other speech or silence.

    venv\\Scripts\\python.exe test_voice_wake.py
"""

import os
import time

import numpy as np
import torch  # noqa: F401  (loads CUDA/cuDNN DLLs for onnxruntime-gpu first, as in live_duplex)
from kokoro_onnx import Kokoro

from live_duplex import KOKORO_DIR, Addressed

LINES = ["Yes, I can hear you.", "The terminal shows that the build failed with exit code 4517.",
         "Google Chrome is open, with your email in the first tab."]


def kokoro(provider):
    os.environ["ONNX_PROVIDER"] = provider
    k = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    k.create("warm up.", voice="am_michael", speed=1.2, lang="en-us")
    times = []
    for line in LINES:
        t = time.perf_counter()
        s, rate = k.create(line, voice="am_michael", speed=1.2, lang="en-us")
        times.append((time.perf_counter() - t, len(s) / rate))
    print(f"{provider:22s} on {k.sess.get_providers()[0]:22s} "
          + " | ".join(f"{a:.2f}s for {b:.1f}s audio" for a, b in times))
    return k


def resample_16k(x):
    n = int(len(x) * 16000 / 24000)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def fires(addr, audio16):
    pad = np.zeros(16000, np.float32)
    x = np.concatenate([pad, audio16 * 0.3, pad])  # ~ a normal mic level
    hit = None
    for i in range(0, len(x), 1600):  # 100 ms blocks, like live_duplex's mic callback
        s = addr.feed(x[i:i + 1600])
        hit = hit or s
    return hit


def main():
    kokoro("CPUExecutionProvider")
    k = kokoro("CUDAExecutionProvider")
    addr = Addressed(wake=True)
    addr.chord = lambda: False
    results = {}
    for voice in ("am_michael", "af_heart", "bm_george"):
        for text, want in (("Hey Fred.", True), ("Hey Fred, what's on my screen?", True),
                           ("Hey Ted, what time is it?", False), ("The build finished without errors.", False)):
            s, _ = k.create(text, voice=voice, speed=1.0, lang="en-us")
            got = fires(addr, resample_16k(s))
            results[(voice, text)] = (bool(got), want)
            print(f"  wake {voice:10s} {text!r:40s} fired={bool(got)} (score {got or 0:.2f}) want={want}")
            time.sleep(WAKE_GAP)
    silent = fires(addr, np.zeros(5 * 16000, np.float32))
    print(f"  wake on 5 s silence: fired={bool(silent)}")
    ok = sum(g == w for g, w in results.values())
    print(f"wake word: {ok}/{len(results)} as expected (synthetic voices; FRED's own benchmark measured 33% recall "
          "on held-out synthetic positives, so misses here are the model, not the wiring)")


WAKE_GAP = 2.1  # FRED's refire cooldown is 2.0 s

if __name__ == "__main__":
    main()
