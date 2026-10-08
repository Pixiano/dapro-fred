"""P1a gate harness: replay labelled recordings through Mic -> WakeDetector + Vad exactly as live, report hit / false-fire rates.

  python tests/replay_audio.py [--data DIR] [--neg-limit N]
DIR holds the labelled sets (default: Old/Core/data/wakeword_training, read in place, never copied into the repo):
  real_positive/ and output/hey_fred/positive_test/   clips that contain the wake phrase  -> expected: audio.wake fires
  real_negative/ and output/hey_fred/negative_test/   clips that do not                   -> expected: no audio.wake
Reports counts and rates only (no file names beyond the missed/false ones' basenames, no audio content). Uses the real frozen model on CPU.
Each clip starts from a reset detector and 0.5 s of leading silence, like a fresh listen.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from audio.mic import Mic
from audio.vad import Vad
from audio.wakeword import WakeDetector
from audio.wav import read16k

DEFAULT = Path(__file__).resolve().parents[4] / "Old" / "Core" / "data" / "wakeword_training"


def run_clip(mic, det, vad, bus_log, samples):
    det.reset()
    vad.reset()
    bus_log.clear()
    lead = np.zeros(8000, dtype=np.int16)
    mic.feed_array(np.concatenate([lead, samples, np.zeros(16000, dtype=np.int16)]), ts=1000.0)
    errs = [e for e in bus_log if e.kind in ("wake_error", "subscriber_error")]
    if errs:
        raise SystemExit(f"detector failed, results would be meaningless: {errs[0].data}")
    wakes = [e for e in bus_log if e.kind == "wake"]
    return len(wakes), max([e.data["score"] for e in bus_log if e.kind in ("wake", "wake_near")] + [0.0]), any(e.kind == "vad" and e.data["speaking"] for e in bus_log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DEFAULT))
    ap.add_argument("--neg-limit", type=int, default=0, help="0 = all negatives")
    a = ap.parse_args()
    root = Path(a.data)
    bus = ev.EventBus()
    log = []
    bus.subscribe(log.append, sources={"audio"})
    mic, det, vad = Mic(bus, device=""), WakeDetector(bus), Vad(bus)
    det.attach(mic)
    vad.attach(mic)
    sets = {"positive": [root / "real_positive", root / "output/hey_fred/positive_test"], "negative": [root / "real_negative", root / "output/hey_fred/negative_test"]}
    print(f"threshold {config.get().wakeword_threshold}, cooldown {config.get().wake_cooldown_s}s, model {Path(config.get().wakeword_model).name}")
    for label, dirs in sets.items():
        for d in dirs:
            files = sorted(d.glob("*.wav"))
            if label == "negative" and a.neg_limit:
                files = files[::max(1, len(files) // a.neg_limit)][:a.neg_limit]
            if not files:
                print(f"{label:9} {d.name}: no files")
                continue
            t0, fired, vad_on, peaks, odd = time.time(), 0, 0, [], []
            for f in files:
                n, peak, speech = run_clip(mic, det, vad, log, read16k(f))
                fired += n > 0
                vad_on += speech
                peaks.append(peak)
                if (label == "positive") != (n > 0):
                    odd.append(f.name)
            ok = fired if label == "positive" else len(files) - fired
            print(f"{label:9} {str(d.relative_to(root)):34} {len(files):5} clips  wake fired {fired:5} ({fired / len(files):6.1%})  vad saw speech {vad_on / len(files):6.1%}  "
                  f"{'recall' if label == 'positive' else 'correct rejects'} {ok / len(files):6.1%}  median peak score {np.median(peaks):.2f}  {time.time() - t0:.0f}s")
            if odd:
                print(f"          {'missed' if label == 'positive' else 'false fires'} (first 5): {odd[:5]}")


if __name__ == "__main__":
    main()
