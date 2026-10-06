"""Voice ID evaluation on Vatsal's recordings (read in place, numbers only are printed, nothing is copied, logged or enrolled for real).

  python tests/voice_eval.py [DATASET_DIR]      DATASET_DIR holds processed/wavs/<source>_segNNNN.wav (default: the Vatsal_v1 set, see DATASET below)
Four measurements, all with a throw-away enrollment in a temp file:
  1. same speaker, other recording: enroll from the segments of some source recordings, test on segments of the others (the cross-session case)
  2. length dependence: the same held-out segments cut to 0.6 / 1 / 2 / 4 s (a spoken command is short)
  3. other voices: synthetic Kokoro voices from the old wake-word negatives (easy imposters; real strangers sit closer, so treat the false-accept column as a floor)
  4. domain gap: the same enrollment against Vatsal's real live-mic "Hey FRED" clips (the dataset is speech-enhanced studio audio, the live mic is not)
Prints score percentiles and false reject / false accept per threshold; then pick `voice_threshold`, `voice_min_s`.
"""
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from audio.wav import read16k
from clock.voice_id import VoiceId

DATASET = Path(r"C:\Users\Dhiraj Vatsal\VatsalDaPro\Projects\Claude\Project_COPY\Data\Vatsal_v1")
OLD = Path(__file__).resolve().parents[4] / "Old" / "Core" / "data" / "wakeword_training"
TS = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6)


def pct(a):
    return " ".join(f"p{p}={np.percentile(a, p):.2f}" for p in (5, 25, 50, 75, 95))


def table(me, other, label_me, label_other):
    print(f"  threshold  false-reject({label_me})  false-accept({label_other})")
    for t in TS:
        print(f"     {t:.2f}        {np.mean(me < t):6.1%}                {np.mean(other >= t):6.1%}")


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else DATASET
    segs = defaultdict(list)
    for f in sorted((root / "processed" / "wavs").glob("*.wav")):
        segs[f.stem.rsplit("_seg", 1)[0]].append(f)
    srcs = sorted(segs)
    print("sources:", {s: len(segs[s]) for s in srcs})
    test_src = srcs[len(srcs) // 2:: 2] or srcs[-1:]                      # every second source from the middle is held out entirely
    train_src = [s for s in srcs if s not in test_src]
    train = [read16k(f) for s in train_src for f in segs[s][::3]][:150]     # a sample of segments, 3 s or longer
    held = [f for s in test_src for f in segs[s][::4]][:80]
    v = VoiceId(Path(tempfile.mkdtemp()) / "v.json")
    n = v.enroll(train)
    print(f"enrolled {n} references from {len(train)} segments of {train_src}; held-out sources {test_src}: {len(held)} segments")

    held_a = [read16k(f) for f in held]
    other_f = sorted((OLD / "negative_speech").glob("*.wav"))
    voices = defaultdict(list)
    for f in other_f:
        voices[f.stem.split("_", 1)[1].rsplit("_", 1)[0]].append(f)
    imp = [read16k(fs[i]) for fs in voices.values() for i in range(0, len(fs), max(1, len(fs) // 2))][:150]
    other = np.array([s for s in (v.score(a) for a in imp) if s is not None])

    print("\n1. same speaker, other recording (full segments, 3-14 s):")
    me = np.array([v.score(a) for a in held_a])
    print(f"  n={len(me)} {pct(me)} min={me.min():.2f}")
    print("\n2. length dependence (held-out segments cut to the first N seconds), vs synthetic other voices cut the same way:")
    for sec in (0.6, 1.0, 2.0, 4.0):
        m = np.array([s for s in (v.score(a[:int(16000 * sec)]) for a in held_a) if s is not None])
        o = np.array([s for s in (v.score(a[:int(16000 * sec)]) for a in imp) if s is not None])
        print(f"  {sec:3.1f}s: me {pct(m)} | others max={o.max():.2f} p95={np.percentile(o, 95):.2f} | at 0.30: reject {np.mean(m < 0.3):5.1%} accept {np.mean(o >= 0.3):5.1%}; at 0.35: reject {np.mean(m < 0.35):5.1%} accept {np.mean(o >= 0.35):5.1%}")
    print(f"\n3. other voices, full clips (n={len(other)}): {pct(other)} max={other.max():.2f}")
    table(me, other, "me", "others")
    live = sorted((OLD / "real_positive").glob("*.wav"))
    lv = np.array([s for s in (v.score(read16k(f)) for f in live) if s is not None])
    print(f"\n4. domain gap: enrollment from the dataset vs {len(lv)} real live-mic 1-s clips: {pct(lv)} min={lv.min():.2f}")
    table(lv, other[:0] if False else np.array([s for s in (v.score(a[:16000]) for a in imp) if s is not None]), "live me", "others(1s)")
    print(f"\nconfigured: voice_threshold={config.get().voice_threshold} voice_min_s={config.get().voice_min_s} voice_refs_cap={config.get().voice_refs_cap}")


if __name__ == "__main__":
    main()
