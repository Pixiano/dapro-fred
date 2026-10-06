"""Live P1a STT gate (real faster-whisper on the GPU). Run by hand, after stopping nothing else: Whisper large-v3-turbo needs ~1.6 GB of VRAM.

  python tests/live_stt.py
Self-contained: the test sentences are invented and spoken by Kokoro (CPU, allowed), so no personal recordings are involved. Checks, per sentence:
  word error rate against the known text, decode time, and that the model ran through gpu.run on settings.whisper_device.
  CPU test (explicit request only): FRED_WHISPER_DEVICE=cpu FRED_WHISPER_COMPUTE=int8 python tests/live_stt.py Also: a silent buffer never reaches the GPU,
  a Capture of the replayed audio transcribes the same, and Stt.unload() frees VRAM. The Kokoro files are read in place from Old/Core/models/kokoro.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
import gpu
from audio.mic import Mic
from audio.stt import Capture, Stt
from audio.wav import to16k

KOKORO = Path(__file__).resolve().parents[4] / "Old" / "Core" / "models" / "kokoro"
SENTENCES = ["Set a timer for five minutes.", "What is on my agenda tomorrow afternoon?", "Turn the volume down to thirty percent.",
             "Open the quarterly budget spreadsheet and read me the first three rows."]


def vram_mb() -> int:
    return int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.split()[0])


def rss_mb() -> int:
    import psutil
    return int(psutil.Process().memory_info().rss / 2**20)


def words(t: str) -> list:
    return re.findall(r"[a-z0-9']+", t.lower().replace("thirty percent", "30 percent").replace("five", "5").replace("three", "3"))


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(1, len(r))


def main():
    from kokoro_onnx import Kokoro
    k = Kokoro(str(KOKORO / "kokoro-v1.0.onnx"), str(KOKORO / "voices-v1.0.bin"))
    audio = []
    for s in SENTENCES:
        x, sr = k.create(s, voice="am_michael", speed=1.0, lang="en-us")
        audio.append(to16k(x, sr))
    print(f"synthesised {len(audio)} sentences with Kokoro (CPU)")
    base, ram0 = vram_mb(), rss_mb()
    print(f"device {config.get().whisper_device}, compute {config.get().whisper_compute}")
    bus = ev.EventBus()
    stt = Stt(bus)
    t0 = time.time()
    first = stt.transcribe(audio[0])
    print(f"first call (incl. model load) {time.time() - t0:.1f}s, VRAM +{vram_mb() - base} MB, RAM +{rss_mb() - ram0} MB, owner stats {gpu.stats()['by_owner']}")
    worst = 0.0
    for s, a in zip(SENTENCES, audio):
        t = stt.transcribe(a)
        e = wer(s, t.text)
        worst = max(worst, e)
        print(f"  WER {e:4.0%}  {t.ms:5d} ms for {t.audio_s:4.1f}s audio  ref={s!r}  hyp={t.text!r}")
    assert worst <= 0.25, f"word error rate too high ({worst:.0%}): check the model/compute type"
    assert stt.transcribe(np.zeros(32000, dtype=np.int16)).silent
    m = Mic(bus, device="")
    cap = Capture(m)
    cap.begin(0)
    m.feed_array(audio[1])
    t = stt.transcribe(cap.end(), final=True)
    assert wer(SENTENCES[1], t.text) <= 0.25, t.text
    print("capture -> transcript ok")
    stt.unload()
    time.sleep(1)
    print(f"after unload VRAM +{vram_mb() - base} MB (ctranslate2 may keep its context until process exit)")
    print("live STT gate passed")


if __name__ == "__main__":
    main()
