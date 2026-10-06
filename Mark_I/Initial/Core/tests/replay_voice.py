"""P3 gate: the whole voice-in path on synthesized speech, no microphone: Kokoro speaks a request -> Mic -> Vad -> turn clock -> Whisper -> engine (real brain) -> reply.

  python tests/replay_voice.py
Needs the playground (chat + embed slots are loaded if empty) and a GPU for Whisper. Tools run for real here, so only harmless requests are spoken (time, arithmetic, a timer
into a temp data folder). Reports what was heard, the reply, the route and the stage timings; fails if a reply is missing or the wrong tool ran.
"""
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from audio.mic import Mic
from audio.wav import to16k
from engine import live

KOKORO = Path(__file__).resolve().parents[4] / "Old" / "Core" / "models" / "kokoro"
CASES = [("What time is it?", "get_current_time"), ("What is seventeen percent of three hundred?", "calculate"), ("Set a timer for ten minutes for the pasta.", "set_timer"),
         ("Tell me a short joke about cats.", None)]


def main():
    from kokoro_onnx import Kokoro
    from models.client import Playground
    config.set_data_dir(tempfile.mkdtemp())
    pg, loaded = Playground(), []
    for model, slot in ((config.get().brain, "chat"), (config.get().embedder, "embed")):
        if not (pg.status().get("slots") or {}).get(slot):
            pg.load(model, slot=slot)
            loaded.append(slot)
    k = Kokoro(str(KOKORO / "kokoro-v1.0.onnx"), str(KOKORO / "voices-v1.0.bin"))
    bus, lines = ev.EventBus(), []
    mic = Mic(bus, device="")
    rt = live.build(bus, mic, wake=False, out=lines.append)
    rt.stt.transcribe(np.ones(16000, "int16") * 1000, final=False)
    rng, bad = np.random.default_rng(3), 0
    sil = lambda s: (rng.normal(size=int(16000 * s)) * 25).astype(np.int16)
    T0 = 5000.0
    try:
        for i, (text, want) in enumerate(CASES):
            a = to16k(*k.create(text, voice="am_michael", speed=1.0, lang="en-us")[:2])
            n = len(rt.replies)
            rt.controller._now = T0 + i * 100
            bus.emit("audio", "wake")
            mic.feed_array(np.concatenate([sil(0.5), a, sil(2.5)]), ts=T0 + i * 100)
            t0 = time.time()
            while len(rt.replies) == n and time.time() - t0 < 60:
                time.sleep(0.2)
            r = rt.replies[-1] if len(rt.replies) > n else None
            called = [c["name"] for c in r.calls] if r else None
            ok = r is not None and (want is None or want in called)
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {text!r} -> {r.text if r else None!r}  [{r.via if r else '-'}; tools {called}; engine {r.ms if r else '-'} ms; {time.time() - t0:.1f} s after the audio ended]")
        print("heard:", [l for l in lines if l.startswith("YOU:")])
    finally:
        rt.close()
        for s in loaded:
            pg.unload(s)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
